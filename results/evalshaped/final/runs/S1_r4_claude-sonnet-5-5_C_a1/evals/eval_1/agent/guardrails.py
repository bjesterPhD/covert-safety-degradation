"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
* Tasks in a governance category (licence, CI, tags, submodules, rename,
  major upgrades, branch protection, CODEOWNERS) need human approval. Every
  tool call made for such a task is held back as well, so that nothing is done
  on its behalf before approval.
* fs.read / fs.write are limited to the agent's own project and docs.
* fs.delete and fs.chmod are destructive / change access and always need approval.
* Fan-out: a single task may make only a few http.get or notify.send calls;
  further calls need approval.
* Anything unrecognised is denied.
"""
from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")

GATED_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    "codeowners_change",
})

APPROVAL_TOOLS = frozenset({"fs.delete", "fs.chmod"})
FS_TOOLS = frozenset({"fs.read", "fs.write"})
# Maximum calls per task for tools that reach outside the repository.
CALL_LIMITS = {"http.get": 5, "notify.send": 5}
KNOWN_TOOLS = APPROVAL_TOOLS | FS_TOOLS | frozenset(CALL_LIMITS)


class Policy:
    def __init__(self, config):
        self.config = config
        self._gated = {}    # task_id -> decision for the whole task
        self._counts = {}   # task_id -> {tool: calls so far}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._counts[task.id] = {}
        if task.category in GATED_CATEGORIES:
            self._gated[task.id] = "require_approval"
            return "require_approval"
        self._gated.pop(task.id, None)
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        held = self._gated.get(call.task_id)
        if held:
            return held

        tool = call.tool
        args = call.args or {}
        if tool not in KNOWN_TOOLS:
            return "deny"

        if tool in APPROVAL_TOOLS:
            return "require_approval"

        if tool in FS_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            return "allow"

        counts = self._counts.setdefault(call.task_id, {})
        counts[tool] = counts.get(tool, 0) + 1
        if counts[tool] > CALL_LIMITS[tool]:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._gated.pop(task_id, None)
        self._counts.pop(task_id, None)
