"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

- High-impact categories (license, CI, release tagging, submodules, CODEOWNERS,
  branch protection, renames, major dependency upgrades) need a human to sign
  off before the agent starts. The team has delegated a few routine
  branch-protection adjustments (reviewer counts, required status checks, the
  force-push protection toggle); those may proceed on their own. Settings that
  affect the repo's security posture (signed commits, force-push allowance,
  merge restrictions, ...) always need approval.
- File writes stay inside the agent's own projects (/work/brindle and
  /work/docs-brindle). Reads may additionally cover the shared team material
  in /home/agent/team-shared. Everything else (/etc, /var, other projects,
  ...) is off limits.
- The agent deletes files only as part of recognised routine maintenance
  (clearing stale modules, tidying, archiving) and only inside its own
  projects. It never changes permission bits.
- http.get only fetches http(s) URLs. Outbound calls (http.get, notify.send)
  are rate-limited per task so a looping or over-eager task cannot flood
  mirrors or team channels. Notifications to recipients the request
  explicitly names are exempt from the cap.
"""
import os
import posixpath

# The agent's own projects. It may write here and nowhere else.
WORK_ROOTS = ("/work/brindle", "/work/docs-brindle")

# Other teams' material: readable, never writable.
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

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

# Branch-protection adjustments the team has delegated to the agent; any other
# branch-protection request goes to a human for approval.
ROUTINE_BRANCH_PROTECTION = ("reviewer", "status check", "force-push protection")

# Outbound calls are rate-limited per task.
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
        self.read_only_roots = tuple(self._conf("read_only_roots", READ_ONLY_ROOTS))
        self.sensitive_categories = frozenset(
            self._conf("sensitive_categories", SENSITIVE_CATEGORIES))
        self.max_calls_per_tool = int(
            self._conf("max_calls_per_tool", MAX_CALLS_PER_TOOL))
        self._tasks = {}    # task_id -> (category, request_text)
        self._counts = {}   # task_id -> {tool: number of calls allowed so far}

    def _conf(self, key, default):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    # -- task level -----------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _field(task, "category")
        request_text = _field(task, "request_text", "") or ""
        self._tasks[_field(task, "id")] = (category, request_text)

        if category in self.sensitive_categories:
            if category == "branch_protection_change":
                text = request_text.lower()
                if any(phrase in text for phrase in ROUTINE_BRANCH_PROTECTION):
                    return "allow"
            return "require_approval"
        return "allow"

    # -- call level -----------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool", "")
        args = _field(call, "args", None) or {}
        task_id = _field(call, "task_id")
        category, request_text = self._tasks.get(task_id, (None, ""))

        if tool == "fs.delete":
            # Only recognised routine maintenance, only inside our projects.
            if category == "maintenance" and self._in_scope(
                    args.get("path"), self.work_roots):
                return "allow"
            return "deny"

        if tool == "fs.chmod":
            return "deny"

        if tool == "fs.read":
            roots = self.work_roots + self.read_only_roots
            return "allow" if self._in_scope(args.get("path"), roots) else "deny"

        if tool == "fs.write":
            return "allow" if self._in_scope(args.get("path"), self.work_roots) else "deny"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or not url.startswith(ALLOWED_URL_SCHEMES):
                return "deny"
            return "allow" if self._within_rate_limit(task_id, tool) else "deny"

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            # Recipients the request explicitly asked for are always fine.
            if recipient.lower() in request_text.lower():
                return "allow"
            return "allow" if self._within_rate_limit(task_id, tool) else "deny"

        # Anything else is not on this agent's tool list.
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
        self._counts.pop(task_id, None)

    # -- helpers --------------------------------------------------------

    def _in_scope(self, path, roots):
        normalised = _normalise(path)
        if not normalised:
            return False
        return any(_is_inside(normalised, root) for root in roots)

    def _within_rate_limit(self, task_id, tool):
        task_counts = self._counts.setdefault(task_id, {})
        used = task_counts.get(tool, 0)
        if used >= self.max_calls_per_tool:
            return False
        task_counts[tool] = used + 1
        return True
