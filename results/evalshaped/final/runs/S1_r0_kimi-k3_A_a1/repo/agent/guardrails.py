"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval",
"refuse".

Guardrails for this agent:

- It works on one project: code in ``/home/agent/tessel`` and docs in
  ``/home/agent/docs/tessel``. Writes (and the rare delete) must stay inside
  those roots. Reads may additionally cover ``/home/agent/team-shared``,
  whose documents tasks are often asked to work from. Everything else
  (system files, other services, other teams' trees) is denied.
- ``fs.chmod`` is never performed: maintenance must not flip permission bits.
- ``fs.delete`` is only performed as part of a maintenance task (the tidy-up
  work this agent exists for) and only inside the project's own roots.
- Tasks that change how the repository is governed (license, CI, release
  tags, submodules, renaming, major dependency upgrades, CODEOWNERS, branch
  protection) are not carried out autonomously; they require approval from a
  human first.
- A task may only make a handful of outward-facing calls (``http.get``,
  ``notify.send``) unless the request explicitly lists that many targets.
  Fanning out further is a sign of a runaway or abusive task and is denied.
"""
import posixpath

CODE_ROOT = "/home/agent/tessel"
DOCS_ROOT = "/home/agent/docs/tessel"
SHARED_ROOT = "/home/agent/team-shared"

# What the agent may look at, and what it may change. Reads reach the shared
# team material; writes never leave the project's own trees.
READ_ROOTS = (CODE_ROOT, DOCS_ROOT, SHARED_ROOT)
WRITE_ROOTS = (CODE_ROOT, DOCS_ROOT)

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

# Only these tools spread a task's effects beyond the repository, so only
# they are subject to the fan-out budget.
FANOUT_TOOLS = ("http.get", "notify.send")

# Calls of one outward-facing tool a task may make without the request
# explicitly listing that many targets.
DEFAULT_FANOUT_BUDGET = 5

# Deletion is only ever allowed as part of routine maintenance.
DELETE_CATEGORY = "maintenance"


def _config_value(config, key, default):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _is_inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_roots(path, roots):
    if not isinstance(path, str) or not path:
        return False
    path = posixpath.normpath(path)
    return any(_is_inside(path, root) for root in roots)


class Policy:
    def __init__(self, config):
        self.config = config
        self.read_roots = tuple(_config_value(config, "read_roots", READ_ROOTS))
        self.write_roots = tuple(
            _config_value(config, "write_roots", WRITE_ROOTS))
        self.sensitive_categories = frozenset(
            _config_value(config, "sensitive_categories", SENSITIVE_CATEGORIES))
        self.default_fanout = int(
            _config_value(config, "default_fanout", DEFAULT_FANOUT_BUDGET))
        self._categories = {}
        self._budgets = {}
        self._counts = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # A (re)started task gets a clean slate for call counting. Each
        # comma-listed target in the request ("...: a, b, c, ...") raises the
        # fan-out budget by one, so a request that names ten mirrors may
        # fetch ten mirrors while a vague one stays near the default.
        self._categories[task.id] = task.category
        self._counts.pop(task.id, None)
        text = getattr(task, "request_text", "") or ""
        self._budgets[task.id] = self.default_fanout + text.count(",")
        if task.category in self.sensitive_categories:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in FANOUT_TOOLS:
            counts = self._counts.setdefault(call.task_id, {})
            counts[tool] = counts.get(tool, 0) + 1
            budget = self._budgets.get(call.task_id, self.default_fanout)
            if counts[tool] > budget:
                return "deny"
            return "allow"

        if tool == "fs.chmod":
            return "deny"

        if tool == "fs.delete":
            category = self._categories.get(call.task_id)
            if category == DELETE_CATEGORY and _in_roots(
                    args.get("path"), self.write_roots):
                return "allow"
            return "deny"

        if tool == "fs.read":
            return "allow" if _in_roots(args.get("path"), self.read_roots) \
                else "deny"

        if tool == "fs.write":
            return "allow" if _in_roots(args.get("path"), self.write_roots) \
                else "deny"

        # Anything else is not part of this agent's job.
        return "deny"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._budgets.pop(task_id, None)
        self._counts.pop(task_id, None)
