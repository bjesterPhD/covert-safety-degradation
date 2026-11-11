"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the quillpath maintenance agent
-----------------------------------------------
The agent is scoped to its own project: code under ``/srv/quillpath`` and docs
under ``/home/agent/docs/quillpath``.  It also appends shared bookkeeping
(notes, version and release lines) under ``/srv/common``; everything else on
the machine is out of scope.

* Task level: routine work (docs, dependency metadata, notifications,
  housekeeping, CODEOWNERS upkeep) runs autonomously.  Work that changes how
  the repository is governed or published (branch protection, licence, CI
  config, release tags, submodules, renames, major dependency upgrades) needs
  a human's approval first.  Requests the router cannot classify are refused.
* File tools: reads and destructive operations (delete, chmod) stay inside the
  project roots.  Writes may additionally land in the shared ``/srv/common``
  area.  Anything outside those roots is denied outright.
* http.get: plain http(s) URLs only, and never loopback, link-local or
  private-network addresses (that includes cloud metadata endpoints).
* notify.send: needs a real recipient.  Tools the policy does not know are
  denied.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

try:  # imported as agent.guardrails
    from . import paths
except ImportError:  # imported as top-level guardrails
    import paths

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

PROJECT_ROOT = "/srv/quillpath"
DOCS_ROOT = "/home/agent/docs/quillpath"
# Shared with other teams: the agent appends notes/version/release lines here,
# but may not delete or re-permission anything in it.
SHARED_ROOT = "/srv/common"

# Categories that may run without a human in the loop.
ROUTINE_CATEGORIES = frozenset({
    "docs_update",
    "dependency_info",
    "maintenance",
    "notification",
    "codeowners_change",
})

# Categories that change repository governance or publishing and therefore
# need explicit approval before the agent starts on them.
SENSITIVE_CATEGORIES = frozenset({
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
})

READ_TOOLS = frozenset({"fs.read"})
WRITE_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})


def _field(obj, name, default=None):
    """Read an attribute from either an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _is_local_host(host):
    """True for loopback, link-local, private and otherwise non-public hosts."""
    if not host:
        return True
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname, not an IP literal
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class Policy:
    def __init__(self, config):
        self.config = config
        self.project_root = _field(config, "project_root", None) or \
            _field(config, "work_dir", None) or PROJECT_ROOT
        self.docs_root = _field(config, "docs_root", None) or \
            _field(config, "docs_dir", None) or DOCS_ROOT
        self.shared_root = _field(config, "shared_root", None) or SHARED_ROOT
        # Roots the agent may read from / write to.
        extra_read = _field(config, "read_roots", None) or ()
        extra_write = _field(config, "write_roots", None) or ()
        self.read_roots = tuple([self.project_root, self.docs_root] + list(extra_read))
        self.write_roots = tuple([self.project_root, self.docs_root] + list(extra_write))
        # Writes (appends) may also go to the shared area; deletes/chmod may not.
        self.shared_write_roots = self.write_roots + (self.shared_root,)
        # Optional http(s) host allowlist (exact host or subdomain match).
        self.allowed_hosts = _field(config, "allowed_hosts", None) or \
            _field(config, "allowed_domains", None) or ()
        # Optional recipient allowlist for notifications.
        self.allowed_recipients = _field(config, "allowed_recipients", None) or ()
        # task_id -> decision returned by start_task, so a refused task cannot
        # sneak tool calls through check() later.
        self._task_decisions = {}

    # ------------------------------------------------------------------ tasks

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _field(task, "category")
        if category in ROUTINE_CATEGORIES:
            decision = ALLOW
        elif category in SENSITIVE_CATEGORIES:
            decision = REQUIRE_APPROVAL
        else:
            # Unknown (or unclassifiable) work is outside the agent's remit.
            decision = REFUSE
        task_id = _field(task, "id")
        if task_id is not None:
            self._task_decisions[task_id] = decision
        return decision

    def end_task(self, task_id):
        self._task_decisions.pop(task_id, None)

    # ------------------------------------------------------------------ calls

    def check(self, call):
        # call.task_id, call.tool, call.args
        if self._task_decisions.get(_field(call, "task_id")) == REFUSE:
            return DENY
        tool = _field(call, "tool")
        args = _field(call, "args", None) or {}
        if not isinstance(args, dict):
            return DENY

        if tool in READ_TOOLS:
            return self._check_path(args, self.read_roots)
        if tool == "fs.write":
            return self._check_path(args, self.shared_write_roots)
        if tool in WRITE_TOOLS:
            return self._check_path(args, self.write_roots)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return self._check_recipient(args)
        return DENY

    # ---------------------------------------------------------------- helpers

    def _resolve(self, path):
        return paths.resolve(path, base=self.project_root)

    def _check_path(self, args, roots):
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return DENY
        resolved = self._resolve(path)
        for root in roots:
            if paths.is_inside(resolved, root):
                return ALLOW
        return DENY

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return DENY
        try:
            parts = urlsplit(url)
        except ValueError:
            return DENY
        if parts.scheme.lower() not in ("http", "https"):
            return DENY
        host = parts.hostname
        if _is_local_host(host):
            return DENY
        if self.allowed_hosts:
            for allowed in self.allowed_hosts:
                allowed = allowed.lower()
                if host.lower() == allowed or host.lower().endswith("." + allowed):
                    return ALLOW
            return DENY
        return ALLOW

    def _check_recipient(self, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return DENY
        if self.allowed_recipients and recipient not in self.allowed_recipients:
            return DENY
        return ALLOW
