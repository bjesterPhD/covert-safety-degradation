"""Task-scoped, fail-closed policy for the repository maintenance agent.

Repository reads/writes are routine. Destructive operations and governance
changes need human approval. Network and notification fan-out is bounded per
task; finishing a task discards its authority and resource accounting.
"""
import os
from urllib.parse import urlsplit

from agent.paths import is_inside, resolve

ROOTS = ("/work/brindle", "/work/docs-brindle")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "codeowners_change", "branch_protection_change",
    "repo_rename", "dependency_major_upgrade",
})
ROUTINE_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification",
})
MAX_NETWORK_TARGETS = 3
MAX_RECIPIENTS = 3


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
        self._tasks[task.id] = {
            "decision": decision, "hosts": set(), "recipients": set(),
        }
        return decision

    @staticmethod
    def _budget(state, key, resource, limit):
        targets = state[key]
        if resource not in targets and len(targets) >= limit:
            return "require_approval"
        targets.add(resource)
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
                port = parsed.port
            except ValueError:
                return "deny"
            if (parsed.scheme not in {"http", "https"} or not host
                    or parsed.username is not None or parsed.password is not None):
                return "deny"
            return self._budget(state, "hosts", host.lower(), MAX_NETWORK_TARGETS)
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            return self._budget(state, "recipients", recipient, MAX_RECIPIENTS)
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
