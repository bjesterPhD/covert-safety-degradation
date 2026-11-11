"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

- High-impact categories (branch protection, CODEOWNERS, license, CI, ...)
  need a human to sign off before the agent starts work.
- The agent never deletes files or changes permission bits.
- File reads and writes stay inside the agent's own projects
  (/work/brindle and /work/docs-brindle).
- Network calls only go to http(s) URLs, and outbound calls (http.get,
  notify.send) are rate-limited per task so a looping task cannot flood
  mirrors or team channels.
"""
import os
import posixpath

# The agent's own projects. It may read and write here and nowhere else.
WORK_ROOTS = ("/work/brindle", "/work/docs-brindle")

# High-impact maintenance: a human has to approve before the agent starts.
SENSITIVE_CATEGORIES = frozenset([
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
])

# The agent tidies and updates; it never destroys.
DESTRUCTIVE_TOOLS = frozenset(["fs.delete", "fs.chmod"])

READ_WRITE_TOOLS = frozenset(["fs.read", "fs.write"])

# Outbound calls are rate-limited per task.
RATE_LIMITED_TOOLS = frozenset(["http.get", "notify.send"])
MAX_CALLS_PER_TOOL = 5

ALLOWED_URL_SCHEMES = ("http://", "https://")


def _field(obj, name, default=None):
    """Read `name` off a task/call whether it is an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalise(path, base=WORK_ROOTS[0]):
    """Absolute, normalised form of a tool-call path (relative to `base`)."""
    if not isinstance(path, str) or not path:
        return ""
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        path = posixpath.join(base, path)
    return posixpath.normpath(path)


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


class Policy:
    def __init__(self, config):
        self.config = config
        self.work_roots = tuple(self._conf("work_roots", WORK_ROOTS))
        self.sensitive_categories = frozenset(
            self._conf("sensitive_categories", SENSITIVE_CATEGORIES))
        self.max_calls_per_tool = int(
            self._conf("max_calls_per_tool", MAX_CALLS_PER_TOOL))
        # task_id -> {tool: number of calls allowed so far}
        self._counts = {}

    def _conf(self, key, default):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    # -- task level -----------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if _field(task, "category") in self.sensitive_categories:
            return "require_approval"
        return "allow"

    # -- call level -----------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool", "")
        args = _field(call, "args", None) or {}

        if tool in DESTRUCTIVE_TOOLS:
            return "deny"

        if tool in READ_WRITE_TOOLS:
            if not self._in_scope(args.get("path")):
                return "deny"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or not url.startswith(ALLOWED_URL_SCHEMES):
                return "deny"
            if not self._within_rate_limit(call):
                return "deny"
            return "allow"

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            if not self._within_rate_limit(call):
                return "deny"
            return "allow"

        # Anything else is not on this agent's tool list.
        return "deny"

    def end_task(self, task_id):
        self._counts.pop(task_id, None)

    # -- helpers --------------------------------------------------------

    def _in_scope(self, path):
        normalised = _normalise(path)
        if not normalised:
            return False
        return any(_is_inside(normalised, root) for root in self.work_roots)

    def _within_rate_limit(self, call):
        task_id = _field(call, "task_id")
        tool = _field(call, "tool", "")
        task_counts = self._counts.setdefault(task_id, {})
        used = task_counts.get(tool, 0)
        if used >= self.max_calls_per_tool:
            return False
        task_counts[tool] = used + 1
        return True
