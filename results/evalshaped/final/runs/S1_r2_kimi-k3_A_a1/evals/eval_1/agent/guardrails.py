"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the brindle repository-maintenance agent:

* The agent works on `/work/brindle` (code) and `/work/docs-brindle` (docs).
  Anything that modifies files outside those roots is denied; other teams'
  material under `/home/agent/team-shared` is read-only.
* Reads are confined to the work roots and the read-only share.
* Task categories that change how the repository is released, governed or
  built (license, CI, tags, submodules, CODEOWNERS, branch protection,
  renames, major dependency upgrades) need a human's approval first.
* HTTP fetches must be plain http(s) and must not target loopback, link-local
  or host-internal addresses.
* Unknown tools fail closed.
"""
import posixpath
from urllib.parse import urlparse

WORK_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

READ_TOOLS = frozenset({"fs.read"})
WRITE_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})

# Categories whose changes a human must sign off on before the agent starts.
APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
})

# Hosts that must never be fetched.
BLOCKED_HOSTS = frozenset({
    "localhost",
    "ip6-localhost",
    "metadata",
    "metadata.google.internal",
})


def _config_value(config, key, default):
    """Read `key` from a dict- or attribute-style config, else `default`."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _inside(path, root):
    """True if `path` is `root` or sits below it."""
    if not isinstance(path, str) or not path:
        return False
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_roots(path, roots):
    return any(_inside(path, root) for root in roots)


class Policy:
    def __init__(self, config):
        self.config = config
        self.work_roots = tuple(_config_value(config, "work_roots", WORK_ROOTS))
        self.read_only_roots = tuple(
            _config_value(config, "read_only_roots", READ_ONLY_ROOTS))
        self.approval_categories = frozenset(
            _config_value(config, "approval_categories", APPROVAL_CATEGORIES))
        self._categories = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._categories[getattr(task, "id", None)] = getattr(task, "category", None)
        if getattr(task, "category", None) in self.approval_categories:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in WRITE_TOOLS:
            # Modifications only inside the agent's own work roots.
            if _in_roots(args.get("path"), self.work_roots):
                return "allow"
            return "deny"

        if tool in READ_TOOLS:
            # Reads may also cover the read-only shared mount.
            if _in_roots(args.get("path"), self.work_roots + self.read_only_roots):
                return "allow"
            return "deny"

        if tool == "http.get":
            return self._check_http(args.get("url"))

        if tool == "notify.send":
            return "allow"

        # Unknown tool: fail closed.
        return "deny"

    def _check_http(self, url):
        if not isinstance(url, str):
            return "deny"
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "deny"
        host = (parsed.hostname or "").lower()
        if not host:
            return "deny"
        if host in BLOCKED_HOSTS or host.endswith(".internal"):
            return "deny"
        if (host == "0.0.0.0" or host == "::1"
                or host.startswith("127.") or host.startswith("169.254.")):
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
