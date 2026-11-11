"""Task-scoped authorization for repository maintenance.

Destructive operations and governance changes require human approval. Routine
reads/writes are confined to the project's code and documentation trees. Network
and notification tools have bounded per-task budgets, independent of target.
"""
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, WORK_DIR, is_inside, resolve
from .router import categorize

ROOTS = (WORK_DIR, "/srv/harborkit-docs")
APPROVAL_CATEGORIES = frozenset({
    "codeowners_change", "branch_protection_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
})
OUTBOUND_LIMIT = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Check the request too: a caller-supplied category cannot bypass a gate.
        decision = "allow"
        if (task.category in APPROVAL_CATEGORIES or
                categorize(task.request_text) in APPROVAL_CATEGORIES):
            decision = "require_approval"
        self._tasks[task.id] = {"decision": decision, "http.get": 0,
                                "notify.send": 0}
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        tool, args = call.tool, call.args
        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # Canonicalization also prevents existing symlinks escaping a root.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in ROOTS):
                return "deny"
            if tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if tool == "http.get":
            try:
                url = urlsplit(args.get("url", ""))
                valid = (url.scheme in ("http", "https") and url.hostname
                         and not url.username and not url.password)
            except (ValueError, TypeError):
                return "deny"
            if not valid:
                return "deny"
        elif tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
        else:
            return "deny"
        if state[tool] >= OUTBOUND_LIMIT:
            return "require_approval"
        state[tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
