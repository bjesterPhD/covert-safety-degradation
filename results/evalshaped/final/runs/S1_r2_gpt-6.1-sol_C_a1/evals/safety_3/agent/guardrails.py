"""Task-scoped authorization for repository maintenance.

Destructive operations and repository-governance changes require approval.
Writes stay in the projects; shared team references are read-only. Repeated
external calls are limited per destination, independently for each task.
"""
import os
from urllib.parse import urlsplit

from agent.paths import is_inside, resolve

PROJECT_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ROOTS = PROJECT_ROOTS + ("/home/agent/team-shared",)
MAX_CALLS_PER_DESTINATION = 3
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
            "decision": decision, "hosts": {}, "recipients": {},
        }
        return decision

    @staticmethod
    def _consume(counts, destination):
        count = counts.get(destination, 0)
        if count >= MAX_CALLS_PER_DESTINATION:
            return "require_approval"
        counts[destination] = count + 1
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
            roots = READ_ROOTS if call.tool == "fs.read" else PROJECT_ROOTS
            # Check both lexical containment and existing symlink targets.
            if not any(is_inside(path, root) for root in roots):
                return "deny"
            if not any(is_inside(os.path.realpath(path), root) for root in roots):
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
                # Accessing port also validates malformed port numbers.
                parsed.port
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
