"""Task-scoped, fail-closed authorization for repository maintenance.

Approval decisions do not execute operations. An external approval workflow must
handle them; repeated calls cannot implicitly grant themselves permission.
"""
import os
from urllib.parse import urlsplit

from .paths import is_inside, resolve

ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
APPROVAL_CATEGORIES = frozenset({
    "branch_protection_change", "codeowners_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
})
ALLOWED_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification", "unknown",
})
# Bound external effects per task, not per destination or across all tasks.
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
        self._tasks[task.id] = {"decision": decision, "http.get": 0, "notify.send": 0}
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
        if call.tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # Check both lexical and real paths to prevent traversal and symlink escapes.
            path = resolve(path)
            real = os.path.realpath(path)
            if not any(is_inside(path, root) and is_inside(real, os.path.realpath(root))
                       for root in ROOTS):
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
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    return "deny"
                if parsed.username is not None or parsed.password is not None:
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
