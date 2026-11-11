"""Fail-closed task and tool policy for the repository maintenance agent.

Reads/writes stay in the project's code and docs trees. Destructive operations
and repository governance changes need human approval. External effects have
per-task budgets; a new task never inherits another task's counters.
"""
import os
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
})
ALLOWED_CATEGORIES = frozenset({
    "notification", "docs_update", "dependency_info", "maintenance",
})
EXTERNAL_CALL_LIMIT = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ALLOWED_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
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
            # Resolve symlinks as well as lexical '..' traversal before checking
            # component boundaries. The filesystem executor must also prevent
            # symlink replacement races when opening the path.
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
                        or parsed.username is not None or parsed.password is not None):
                    return "deny"
            except ValueError:
                return "deny"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.startswith("#") or len(recipient) < 2:
                return "deny"
        else:
            return "deny"
        if state[call.tool] >= EXTERNAL_CALL_LIMIT:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
