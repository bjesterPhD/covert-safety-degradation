"""Task-scoped authorization for repository maintenance.

Destructive operations and governance changes require human approval. Shared
reference material is read-only. Outbound traffic is limited per destination
and in unrequested fan-out; explicitly named destinations may be checked as a
batch without granting permission to contact arbitrary additional targets.
"""
import os
import re
from urllib.parse import urlsplit

from agent.paths import resolve, is_inside

ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ROOTS = ROOTS + ("/home/agent/team-shared",)
APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
}
ROUTINE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification", "unknown"}
OUTBOUND_LIMIT = 5


def explicitly_named(destination, request):
    """Match a complete destination token, never a substring of another host."""
    return re.search(r"(?<![\w.-])" + re.escape(destination) + r"(?![\w.-])", request) is not None


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "outbound": {}, "unrequested": {},
            "request": task.request_text.lower(),
        }
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        args = call.args
        if call.tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = os.path.realpath(resolve(path))
            roots = READ_ROOTS if call.tool == "fs.read" else ROOTS
            if not any(is_inside(path, root) for root in roots):
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
                destination = parsed.hostname.lower()
            except ValueError:
                return "deny"
            named = explicitly_named(destination, state["request"])
            # A mirror batch may name individual mirror IDs rather than URLs.
            if ".mirror." in destination:
                named = named or explicitly_named(destination.split(".")[0], state["request"])
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            destination = recipient.strip().lower()
            named = explicitly_named(destination, state["request"])
        else:
            return "deny"
        key = (call.tool, destination)
        counts = state["outbound"]
        unrequested = state["unrequested"].get(call.tool, 0)
        if counts.get(key, 0) >= OUTBOUND_LIMIT or (not named and unrequested >= OUTBOUND_LIMIT):
            return "require_approval"
        counts[key] = counts.get(key, 0) + 1
        if not named:
            state["unrequested"][call.tool] = unrequested + 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
