"""Fail-closed task and tool policy for repository maintenance.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Budgets and
permissions belong to a task, never to the lifetime of the agent.
"""
import os
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve
from agent.router import categorize

PROJECT_ROOTS = (WORK_DIR, "/home/agent/docs/tessel")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
})
ALLOWED_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification",
})
SIDE_EFFECT_LIMIT = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Check the text as well as the supplied routing label.
        categories = {task.category, categorize(task.request_text)}
        if categories & APPROVAL_CATEGORIES:
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
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = call.args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # realpath also prevents escape through existing symlinks.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in PROJECT_ROOTS):
                return "deny"
            if call.tool in {"fs.delete", "fs.chmod"}:
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = call.args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    return "deny"
                if parsed.username is not None or parsed.password is not None:
                    return "deny"
            except ValueError:
                return "deny"
        elif call.tool == "notify.send":
            recipient = call.args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
        else:
            return "deny"
        if state[call.tool] >= SIDE_EFFECT_LIMIT:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
