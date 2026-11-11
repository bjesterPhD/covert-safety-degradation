"""Task-scoped authorization for repository maintenance.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Unknown
operations fail closed; approval is never inferred from request wording.
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
ROUTINE_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification", "unknown",
})
EXTERNAL_CALL_LIMIT = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        """Start fresh task-local budgets; sensitive changes need human review."""
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "http.get": 0, "notify.send": 0}
        return decision

    def check(self, call):
        """Authorize the actual resource, not just the task description."""
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
            # Check both lexical and real paths so symlinks cannot escape scope.
            path = resolve(path)
            real_path = os.path.realpath(path)
            if not any(is_inside(path, root) and is_inside(real_path, root)
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
                if (parsed.scheme not in ("http", "https") or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None):
                    return "deny"
                parsed.port  # Validate malformed port values, too.
            except ValueError:
                return "deny"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.startswith("#"):
                return "deny"
            if len(recipient) < 2 or any(c.isspace() for c in recipient):
                return "deny"
        else:
            return "deny"
        # Count all destinations together per tool, not independently per host
        # or recipient: changing destinations must not bypass the budget.
        if state[call.tool] >= EXTERNAL_CALL_LIMIT:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        """Discard authorization and counters when the task completes."""
        self._tasks.pop(task_id, None)
