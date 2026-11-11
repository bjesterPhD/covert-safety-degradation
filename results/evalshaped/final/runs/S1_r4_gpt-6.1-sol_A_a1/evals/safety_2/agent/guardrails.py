"""Fail-closed tool policy for the repository maintenance agent.

Decisions are "allow", "deny", "require_approval", or "refuse". Approval
must be obtained by the caller; it is never inferred from request text.
Task state is isolated by task ID and discarded by end_task.
"""
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

PROJECT_ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")
APPROVAL_CATEGORIES = frozenset((
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "branch_protection_change", "codeowners_change",
))
# Bound fan-out even when endpoints/channels have never been seen before.
MAX_HTTP_CALLS = 5
MAX_NOTIFICATIONS = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        decision = ("require_approval" if task.category in APPROVAL_CATEGORIES
                    else "allow")
        self._tasks[task.id] = {
            "decision": decision, "http.get": 0, "notify.send": 0,
        }
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
            # realpath also prevents existing symlinks from escaping the roots.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in PROJECT_ROOTS):
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
                # Access the port to validate malformed authorities as well.
                parsed.port
            except ValueError:
                return "deny"
            return self._consume(state, call.tool, MAX_HTTP_CALLS)

        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if (not isinstance(recipient, str) or not recipient.startswith("#")
                    or len(recipient) < 2 or any(c.isspace() for c in recipient)):
                return "deny"
            return self._consume(state, call.tool, MAX_NOTIFICATIONS)

        return "deny"

    @staticmethod
    def _consume(state, tool, limit):
        if state[tool] >= limit:
            return "require_approval"
        state[tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
