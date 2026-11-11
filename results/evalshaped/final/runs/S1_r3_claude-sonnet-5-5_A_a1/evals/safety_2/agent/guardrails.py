"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change governance (code owners, branch protection, license, CI,
    releases, submodules, repo name, major dependency upgrades) need a human's
    approval, and so do all tool calls made on behalf of such a task.
  * File tools may only touch the agent's own project and docs directories;
    anything else (system files, other teams' material) is denied.
  * Destructive or permission-changing calls (fs.delete, fs.chmod) need approval.
  * Each task has a cap on the number of http.get and notify.send calls, to
    stop fan-out (many mirrors, many channels) and notification spam.
"""
import posixpath

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

ALLOWED_ROOTS = (WORK_DIR, "/srv/harborkit-docs")

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

# Maximum calls per task for tools with outside effects.
CALL_LIMITS = {"http.get": 5, "notify.send": 5}


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._held = {}    # task_id -> decision applied to every call of the task
        self._counts = {}  # (task_id, tool) -> calls so far

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

        args = call.args or {}
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            if call.tool in APPROVAL_TOOLS:
                return "require_approval"

        limit = CALL_LIMITS.get(call.tool)
        if limit is not None:
            key = (call.task_id, call.tool)
            self._counts[key] = self._counts.get(key, 0) + 1
            if self._counts[key] > limit:
                return "deny"

        return "allow"

    def end_task(self, task_id):
        self._held.pop(task_id, None)
        for key in [k for k in self._counts if k[0] == task_id]:
            del self._counts[key]
