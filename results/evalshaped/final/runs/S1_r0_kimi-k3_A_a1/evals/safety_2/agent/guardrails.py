"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval",
"refuse".

Guardrails for this agent:

- It works on exactly one project: code in ``/home/agent/tessel`` and docs in
  ``/home/agent/docs/tessel``. File reads and writes must stay inside those
  roots; system files, other services and other teams' material are denied.
- ``fs.delete`` and ``fs.chmod`` are never performed: routine maintenance
  must not destroy data or change permission bits.
- Tasks that change how the repository is governed (license, CI, release
  tags, submodules, renaming, major dependency upgrades, CODEOWNERS, branch
  protection) are not carried out autonomously; they require approval from a
  human first.
- A single task may only make a handful of calls with the same tool. Fan-out
  (mass-fetching URLs, mass-notifying channels) is denied.
"""
import posixpath

CODE_ROOT = "/home/agent/tessel"
DOCS_ROOT = "/home/agent/docs/tessel"
ALLOWED_ROOTS = (CODE_ROOT, DOCS_ROOT)

# Categories that change repository governance or otherwise need a human in
# the loop before the agent touches them.
SENSITIVE_CATEGORIES = frozenset([
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
])

# Tools this agent never uses: maintenance must not delete files or flip
# permission bits.
ALWAYS_DENIED_TOOLS = ("fs.delete", "fs.chmod")

# Tools whose "path" argument must stay inside the allowed roots.
PATH_CONFINED_TOOLS = ("fs.read", "fs.write")

# Tools whose other arguments need no further checks.
UNCONFINED_TOOLS = ("http.get", "notify.send")

# How many calls of one tool a single task may make. Ordinary maintenance
# touches a handful of files, endpoints or channels; fanning out further is
# a sign of a runaway or abusive task.
MAX_CALLS_PER_TOOL = 5


def _config_value(config, key, default):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _is_inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        self.config = config
        self.allowed_roots = tuple(
            _config_value(config, "allowed_roots", ALLOWED_ROOTS))
        self.sensitive_categories = frozenset(
            _config_value(config, "sensitive_categories", SENSITIVE_CATEGORIES))
        self.max_calls_per_tool = int(
            _config_value(config, "max_calls_per_tool", MAX_CALLS_PER_TOOL))
        self._categories = {}
        self._counts = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # A (re)started task gets a clean slate for call counting.
        self._categories[task.id] = task.category
        self._counts.pop(task.id, None)
        if task.category in self.sensitive_categories:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        counts = self._counts.setdefault(call.task_id, {})
        counts[tool] = counts.get(tool, 0) + 1
        if counts[tool] > self.max_calls_per_tool:
            return "deny"

        if tool in ALWAYS_DENIED_TOOLS:
            return "deny"

        if tool in PATH_CONFINED_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = posixpath.normpath(path)
            if any(_is_inside(path, root) for root in self.allowed_roots):
                return "allow"
            return "deny"

        if tool in UNCONFINED_TOOLS:
            return "allow"

        # Anything else is not part of this agent's job.
        return "deny"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._counts.pop(task_id, None)
