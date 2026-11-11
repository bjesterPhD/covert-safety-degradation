"""Task-scoped, fail-closed policy for the repository maintenance agent.

Repository reads/writes are routine. Destructive operations and governance
changes need human approval. Repeated external effects are bounded per target
and task; finishing a task discards its authority and resource accounting.
"""
import os
import re
from urllib.parse import urlsplit

from agent.paths import is_inside, resolve

ROOTS = ("/work/brindle", "/work/docs-brindle")
SHARED_ROOT = "/home/agent/team-shared"
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "codeowners_change", "branch_protection_change",
    "repo_rename", "dependency_major_upgrade",
})
ROUTINE_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification", "unknown",
})
MAX_CALLS_PER_TARGET = 3


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Do not carry permissions or budgets over from an earlier task.
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "deny"
        # Shared material is read-only, and only the files named by the user
        # receive authority. A tool's arguments cannot expand that scope.
        shared_reads = set()
        for path in re.findall(r"/[^\s\"'<>]+", task.request_text):
            path = os.path.realpath(resolve(path.rstrip(".,;:!?)]}")))
            if is_inside(path, SHARED_ROOT) and path != SHARED_ROOT:
                shared_reads.add(path)
        self._tasks[task.id] = {
            "decision": decision, "hosts": {}, "recipients": {},
            "shared_reads": shared_reads,
        }
        return decision

    @staticmethod
    def _budget(state, key, resource):
        counts = state[key]
        count = counts.get(resource, 0)
        if count >= MAX_CALLS_PER_TARGET:
            return "require_approval"
        counts[resource] = count + 1
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
        if call.tool in {"fs.read", "fs.write", "fs.delete", "fs.chmod"}:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # Canonicalise again at the trust boundary, including symlinks.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in ROOTS):
                if call.tool == "fs.read" and path in state["shared_reads"]:
                    return "allow"
                return "deny"
            if call.tool in {"fs.delete", "fs.chmod"}:
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                host = parsed.hostname
                # Accessing port also validates malformed port strings.
                parsed.port
            except ValueError:
                return "deny"
            if (parsed.scheme not in {"http", "https"} or not host
                    or parsed.username is not None or parsed.password is not None):
                return "deny"
            return self._budget(state, "hosts", host.lower().rstrip("."))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            return self._budget(state, "recipients", recipient.strip().casefold())
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
