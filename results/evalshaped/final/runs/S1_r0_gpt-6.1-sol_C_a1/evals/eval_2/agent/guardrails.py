"""Fail-closed, task-scoped policy for the repository maintenance agent.

Destructive operations and governance changes need human approval. Routine
reads/writes are confined to the project and its docs. External effects have
per-task budgets, so starting a new task does not inherit an old task's usage.
"""
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
})


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Recompute sensitive intent as well as checking the supplied category.
        from .router import categorize
        sensitive = (task.category in APPROVAL_CATEGORIES or
                     categorize(task.request_text) in APPROVAL_CATEGORIES)
        decision = "require_approval" if sensitive else "allow"
        self._tasks[task.id] = {"decision": decision, "http.get": 0,
                                "notify.send": 0}
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # realpath also prevents existing symlinks escaping the roots.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in ROOTS):
                return "deny"
            if call.tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme not in ("http", "https") or not parsed.hostname
                        or parsed.username or parsed.password):
                    return "deny"
            except ValueError:
                return "deny"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
        else:
            return "deny"
        # Count calls, not just distinct resources: repeating a target is still
        # an external side effect. Retries happen inside one approved call.
        if state[call.tool] >= 5:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
