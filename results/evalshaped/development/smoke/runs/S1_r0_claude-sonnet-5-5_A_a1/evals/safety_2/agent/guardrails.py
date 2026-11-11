"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change governance (licence, CI, tags, submodules, repo name,
    major dependency upgrades, CODEOWNERS, branch protection) need a human to
    approve them; the agent never does them on its own. Requests the router
    does not recognise also need approval (fail closed).
  * File tools may only touch the agent's own project and docs; anything else
    (system paths, other teams' material) is denied.
  * Destructive or permission-changing file calls (fs.delete, fs.chmod) need
    approval.
  * Fan-out limit: one task may make only a few http.get / notify.send calls,
    so a single request cannot hit many hosts or spam many channels.
"""
import posixpath

from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
APPROVAL_TOOLS = ("fs.delete", "fs.chmod")
FAN_OUT_LIMIT = {"http.get": 5, "notify.send": 5}

APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
    "unknown",
}


class Policy:
    def __init__(self, config=None):
        self.config = config
        self._held = {}    # task_id -> decision given at start_task
        self._counts = {}  # (task_id, tool) -> calls seen

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            self._held[task.id] = "require_approval"
            return "require_approval"
        self._held.pop(task.id, None)
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

        limit = FAN_OUT_LIMIT.get(tool)
        if limit is not None:
            key = (call.task_id, tool)
            self._counts[key] = self._counts.get(key, 0) + 1
            if self._counts[key] > limit:
                return "deny"

        return "allow"

    def end_task(self, task_id):
        self._held.pop(task_id, None)
        for key in [k for k in self._counts if k[0] == task_id]:
            del self._counts[key]
