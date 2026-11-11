"""Policy layer: every task and every tool call passes through here first.

``start_task`` and ``check`` return one of four verdicts:

  "allow" ............ the task or call may proceed
  "require_approval" . hold until a human approves it
  "deny" ............. blocked: outside the agent's scope
  "refuse" ........... blocked and flagged: unsafe, not merely out of scope

Scope
-----
The agent maintains its own project only: the code tree at ``/srv/sablefish``
and the docs tree at ``/home/agent/docs/sablefish``. Everything else on the
machine — including ``/home/agent/team-shared``, which belongs to other
teams — is out of scope.

Task level (``start_task``)
---------------------------
* Routine categories (maintenance, docs_update, dependency_info,
  notification): the agent may run them on its own -> "allow".
* Sensitive categories (license, CI, release tagging, submodule, rename,
  major dependency upgrade, branch protection, CODEOWNERS): these change
  the project in ways that are hard to undo or affect the whole team, so a
  human signs off first -> "require_approval".
* Requests the router could not classify also go to a human rather than
  being run (or dropped) sight unseen -> "require_approval".

Call level (``check``)
----------------------
* ``fs.*`` inside the agent's roots -> "allow", except that the VCS
  internals (``.git``) may not be modified ("deny") and the roots
  themselves may not be deleted or re-permissioned ("refuse").
* ``fs.*`` anywhere else is out of scope -> "deny"; attempting to modify
  system locations (``/etc``, ``/usr``, ...) is unsafe -> "refuse".
* ``http.get`` may only fetch http(s) URLs from public hosts; other
  schemes (``file://``, ...) and loopback/private/link-local hosts are
  refused (SSRF protection).
* ``notify.send`` needs a non-empty recipient.
* Unknown tools fail closed -> "deny".
"""

import ipaddress
import posixpath
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# The agent's own territory.
ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")

# Work the agent may do without waiting for a human.
ROUTINE_CATEGORIES = frozenset({
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
})

# Changes that need a human's sign-off before the task starts.
SENSITIVE_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    "codeowners_change",
})

FS_TOOLS = frozenset({"fs.read", "fs.write", "fs.delete", "fs.chmod"})
MODIFY_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})
KNOWN_TOOLS = FS_TOOLS | {"http.get", "notify.send"}

# System locations the agent must never modify.
SYSTEM_PATHS = (
    "/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var",
    "/boot", "/root", "/proc", "/sys", "/dev", "/run", "/opt", "/snap",
)

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def _normalise(path):
    return posixpath.normpath(path)


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def _is_private_host(host):
    """True for loopback, private, link-local and other non-public hosts."""
    if not host:
        return True
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".lan", ".corp")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.roots = tuple(self.config.get("roots", ROOTS))
        self.routine = frozenset(
            self.config.get("routine_categories", ROUTINE_CATEGORIES))
        self.sensitive = frozenset(
            self.config.get("sensitive_categories", SENSITIVE_CATEGORIES))
        self.system_paths = tuple(
            self.config.get("system_paths", SYSTEM_PATHS))
        # Categories seen in start_task, by task id, until end_task.
        self._tasks = {}

    # ---- task level ---------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        category = getattr(task, "category", None)
        self._tasks[task_id] = category
        if category in self.routine:
            return ALLOW
        # Sensitive categories, and anything the router could not classify,
        # need a human to look before the agent starts work.
        return REQUIRE_APPROVAL

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    # ---- call level ---------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        if tool in FS_TOOLS:
            return self._check_fs(tool, args.get("path"))
        if tool == "http.get":
            return self._check_http(args.get("url"))
        if tool == "notify.send":
            return self._check_notify(args.get("recipient"))
        # Fail closed on tools we do not know.
        return DENY

    def _check_fs(self, tool, path):
        if not isinstance(path, str) or not path:
            return DENY
        path = _normalise(path)
        if any(_is_inside(path, _normalise(root)) for root in self.roots):
            # The project roots themselves may not be deleted or
            # re-permissioned; only their contents may change.
            if tool in ("fs.delete", "fs.chmod") and any(
                    path == _normalise(root) for root in self.roots):
                return REFUSE
            # VCS internals are off-limits to modification.
            if tool in MODIFY_TOOLS and ".git" in path.split("/"):
                return DENY
            return ALLOW
        if tool in MODIFY_TOOLS and any(
                _is_inside(path, p) for p in self.system_paths):
            # /etc, /usr, the filesystem root, ... — actively dangerous.
            return REFUSE
        # Out of scope (this covers /home/agent/team-shared and the rest of
        # the filesystem): the agent maintains only its own project.
        return DENY

    def _check_http(self, url):
        if not isinstance(url, str) or not url:
            return DENY
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
            # file://, gopher://, scheme-less strings, ...
            return REFUSE
        if _is_private_host(parsed.hostname or ""):
            return REFUSE
        return ALLOW

    def _check_notify(self, recipient):
        if isinstance(recipient, str) and recipient.strip():
            return ALLOW
        return DENY
