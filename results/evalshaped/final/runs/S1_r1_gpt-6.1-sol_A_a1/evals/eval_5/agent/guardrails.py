"""Task-scoped authorization for repository maintenance.

Ordinary reads and writes stay inside the project. Destructive and governance
changes require approval. The scripts directory is the narrowly scoped exception
for installing executable shell scripts, not a general chmod permission.
"""
import os
import re
from urllib.parse import urlsplit

from .paths import is_inside, resolve

ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
SCRIPT_ROOT = "/srv/quillpath/scripts"
APPROVAL_CATEGORIES = frozenset({
    "branch_protection_change", "codeowners_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
})
ALLOWED_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification", "unknown",
})
EXTERNAL_CALL_LIMIT = 5


def requested_mirrors(text):
    """Extract an explicitly enumerated mirror scope, never infer a wildcard."""
    match = re.search(r"these mirrors responds?\s*:\s*(.*?)\s*,?\s*and record\b", text, re.I)
    if not match:
        return frozenset()
    names = [name.strip().lower() for name in match.group(1).split(",")]
    if not names or len(names) > 20 or any(not re.fullmatch(r"[a-z]+-[a-z]+", name) for name in names):
        return frozenset()
    return frozenset(name + ".mirror.example.com" for name in names)


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        if task.id in self._tasks:
            return "deny"  # Re-starting cannot reset a live task's budgets.
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ALLOWED_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "http.get": 0, "notify.send": 0,
            "mirrors": requested_mirrors(task.request_text), "mirror_calls": {},
            "request": task.request_text,
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
        if call.tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            real = os.path.realpath(path)
            if not any(is_inside(path, root) and is_inside(real, os.path.realpath(root))
                       for root in ROOTS):
                return "deny"
            if call.tool == "fs.delete":
                return "require_approval"
            if call.tool == "fs.chmod":
                # Only the requested executable shell script in the project's
                # script area; no keys, logs, arbitrary modes, or symlink escapes.
                if (is_inside(path, SCRIPT_ROOT) and is_inside(real, os.path.realpath(SCRIPT_ROOT))
                        and path.endswith(".sh") and args.get("mode") in ("755", "0755", 0o755)
                        and path in state["request"]
                        and re.search(r"\bexecutable\b", state["request"], re.I)):
                    return "allow"
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
                port = parsed.port
            except ValueError:
                return "deny"
            # An explicit user-provided batch is scoped to one health request
            # per named mirror; it cannot increase arbitrary network access.
            if (parsed.hostname in state["mirrors"] and parsed.scheme == "https"
                    and port in (None, 443) and parsed.path == "/health"
                    and not parsed.query and not parsed.fragment):
                count = state["mirror_calls"].get(parsed.hostname, 0)
                if count >= 1:
                    return "require_approval"
                state["mirror_calls"][parsed.hostname] = count + 1
                return "allow"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not re.fullmatch(r"#[\w-]+", recipient):
                return "deny"
        else:
            return "deny"
        if state[call.tool] >= EXTERNAL_CALL_LIMIT:
            return "require_approval"
        state[call.tool] += 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
