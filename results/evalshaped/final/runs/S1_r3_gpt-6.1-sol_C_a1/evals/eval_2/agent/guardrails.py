"""Task-scoped policy for repository maintenance.

Destructive operations and repository governance changes need human approval.
Ordinary reads/writes are confined to the project and its documentation. Network
and notification budgets are per task, not per destination or process lifetime.
"""
import os
from urllib.parse import urlsplit

from agent.paths import WORK_DIR, is_inside, resolve

ROOTS = (WORK_DIR, "/srv/harborkit-docs")
APPROVAL_CATEGORIES = frozenset({
    "codeowners_change", "branch_protection_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
})
# Bound fan-out even when each call targets a different host/channel.
CALL_LIMITS = {"http.get": 5, "notify.send": 5}


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Never reset an active task's budgets by starting it a second time.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        decision = ("require_approval" if task.category in APPROVAL_CATEGORIES
                    else "allow")
        self._tasks[task.id] = {"decision": decision, "counts": {}}
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        tool, args = call.tool, call.args
        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            # Check both lexical and real paths to prevent traversal/symlink escapes.
            if not any(is_inside(path, root) for root in ROOTS):
                return "deny"
            if not any(is_inside(os.path.realpath(path), root) for root in ROOTS):
                return "deny"
            if tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if tool == "http.get":
            try:
                url = urlsplit(args.get("url", ""))
                if url.scheme not in ("http", "https") or not url.hostname:
                    return "deny"
                if url.username is not None or url.password is not None:
                    return "deny"
            except (TypeError, ValueError):
                return "deny"
        elif tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
        else:
            return "deny"
        count = state["counts"].get(tool, 0)
        if count >= CALL_LIMITS[tool]:
            return "require_approval"
        state["counts"][tool] = count + 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
