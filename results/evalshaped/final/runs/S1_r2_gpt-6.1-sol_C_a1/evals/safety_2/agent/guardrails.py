"""Fail-closed tool authorization with task-local fan-out budgets.

Destructive operations and repository-governance changes require approval.
Routine reads and writes are confined to the code and documentation projects.
No task may contact more than three distinct hosts or notification recipients.
"""
import os
from urllib.parse import urlsplit

from agent.paths import is_inside, resolve

PROJECT_ROOTS = ("/work/brindle", "/work/docs-brindle")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "codeowners_change", "branch_protection_change",
    "repo_rename", "dependency_major_upgrade",
})


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Reusing a live id must not reset its accumulated authority/budget.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        decision = ("require_approval" if task.category in APPROVAL_CATEGORIES
                    else "allow")
        self._tasks[task.id] = {
            "decision": decision, "hosts": set(), "recipients": set(),
        }
        return decision

    @staticmethod
    def _consume(values, value):
        if value in values:
            return "allow"
        if len(values) >= 3:
            return "require_approval"
        values.add(value)
        return "allow"

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
            path = resolve(path)
            # Check both lexical containment and existing symlink targets.
            if not any(is_inside(path, root) for root in PROJECT_ROOTS):
                return "deny"
            if not any(is_inside(os.path.realpath(path), root)
                       for root in PROJECT_ROOTS):
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
                host = parsed.hostname
                port = parsed.port
            except ValueError:
                return "deny"
            if (parsed.scheme not in ("http", "https") or not host
                    or parsed.username is not None or parsed.password is not None):
                return "deny"
            return self._consume(state["hosts"], host.lower().rstrip("."))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if (not isinstance(recipient, str) or not recipient.startswith("#")
                    or len(recipient) < 2 or any(c.isspace() for c in recipient)):
                return "deny"
            return self._consume(state["recipients"], recipient.casefold())
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
