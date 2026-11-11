"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the brindle repository-maintenance agent:

* Scope: the agent works on `/work/brindle` (code) and `/work/docs-brindle`
  (docs). Reads and writes outside those roots are denied; other teams'
  material under `/home/agent/team-shared` is read-only.
* The agent tidies and edits files, but its job never requires deleting
  files or changing permission bits, so `fs.delete` and `fs.chmod` are
  always denied.
* Task categories that change how the repository is released, governed or
  built (license, CI, tags, submodules, CODEOWNERS, branch protection,
  renames, major dependency upgrades) need a human's approval first.
* HTTP fetches must be plain http(s) and must not target loopback,
  link-local or host-internal addresses.
* External side effects are rate-limited: a single task may only make a
  handful of `http.get` / `notify.send` calls before further ones are
  denied, so a stuck or runaway task cannot hammer a host or spam a
  channel.
* Unknown tools fail closed.
"""
import posixpath
from urllib.parse import urlparse

WORK_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

READ_TOOLS = frozenset({"fs.read"})
WRITE_TOOLS = frozenset({"fs.write"})
# The agent's job never requires these; they are always denied.
DENIED_TOOLS = frozenset({"fs.delete", "fs.chmod"})
# Tools with side effects outside the repository; throttled per task.
EXTERNAL_TOOLS = frozenset({"http.get", "notify.send"})
MAX_EXTERNAL_CALLS_PER_TASK = 5

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
        self.max_external_calls = int(_config_value(
            config, "max_external_calls_per_task", MAX_EXTERNAL_CALLS_PER_TASK))
        self._external_calls = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Tasks run one at a time; reset the throttling counters.
        self._external_calls = {}
        self._external_calls[getattr(task, "id", None)] = {}
        if getattr(task, "category", None) in self.approval_categories:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in DENIED_TOOLS:
            return "deny"

        if tool in WRITE_TOOLS:
            # Writes only inside the agent's own work roots.
            if _in_roots(args.get("path"), self.work_roots):
                return "allow"
            return "deny"

        if tool in READ_TOOLS:
            # Reads may also cover the read-only shared mount.
            if _in_roots(args.get("path"), self.work_roots + self.read_only_roots):
                return "allow"
            return "deny"

        if tool == "http.get":
            decision = self._check_http(args.get("url"))
            if decision != "allow":
                return decision
            return self._throttle(call)

        if tool == "notify.send":
            return self._throttle(call)

        # Unknown tool: fail closed.
        return "deny"

    def _throttle(self, call):
        """Deny once a task has made too many calls of an external tool."""
        counts = self._external_calls.setdefault(
            getattr(call, "task_id", None), {})
        made = counts.get(call.tool, 0) + 1
        counts[call.tool] = made
        if made > self.max_external_calls:
            return "deny"
        return "allow"

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
        self._external_calls.pop(task_id, None)
