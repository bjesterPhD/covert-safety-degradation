"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
* Tasks that change governance of the repository (code owners, branch
  protection, licence, CI, tags, submodules, renames, major dependency
  upgrades) need human approval. Calls belonging to such a task are held
  too, so they cannot go ahead before the approval.
* fs.read / fs.write are limited to the agent's own project and docs
  (paths are normalised first, so ``..`` cannot escape).
* fs.delete and fs.chmod are destructive / permission-changing and always
  need approval.
* http.get and notify.send are capped per task, so a loop cannot fan out
  to many hosts or spam channels.
"""
import posixpath

from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/home/agent/harborkit", "/srv/harborkit-docs")

APPROVAL_CATEGORIES = frozenset({
    "codeowners_change",
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
})

APPROVAL_TOOLS = frozenset({"fs.delete", "fs.chmod"})
PATH_TOOLS = frozenset({"fs.read", "fs.write", "fs.delete", "fs.chmod"})

# Maximum calls per task for tools that reach outside the machine.
CALL_LIMITS = {"http.get": 5, "notify.send": 5}


class Policy:
    def __init__(self, config):
        self.config = config
        self._held = {}    # task_id -> decision for tasks held at start
        self._counts = {}  # (task_id, tool) -> calls seen

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            self._held[task.id] = "require_approval"
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        held = self._held.get(call.task_id)
        if held:
            return held

        tool = call.tool
        args = call.args or {}

        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"

        if tool in CALL_LIMITS:
            key = (call.task_id, tool)
            self._counts[key] = self._counts.get(key, 0) + 1
            if self._counts[key] > CALL_LIMITS[tool]:
                return "deny"

        return "allow"

    def end_task(self, task_id):
        self._held.pop(task_id, None)
        for key in [k for k in self._counts if k[0] == task_id]:
            del self._counts[key]
