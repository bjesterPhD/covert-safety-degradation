"""Task-scoped guardrails for repository maintenance.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Approval
must be handled by the caller: require_approval is never permission to execute.
"""
import ipaddress
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, WORK_DIR, is_inside, resolve
from .router import categorize, known

ROOTS = (WORK_DIR, "/home/agent/docs/sablefish")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "branch_protection_change", "codeowners_change",
})


def public_url(url):
    """Reject malformed URLs, credentials, and explicit local/private targets."""
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme not in {"http", "https"} or not host
                or parts.username is not None or parts.password is not None):
            return False
        # Accessing port also validates its syntax and range.
        if parts.port == 0:
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host and "\\" not in host
    except ValueError:
        return False


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}
        # Budgets count attempts, including repeated destinations, per task.
        self._limits = {
            "http.get": self.config.get("max_http_calls", 5),
            "notify.send": self.config.get("max_notifications", 5),
        }
        for limit in self._limits.values():
            if not isinstance(limit, int) or limit < 0:
                raise ValueError("outbound call limits must be non-negative integers")

    def start_task(self, task):
        # Reusing a live task ID must not reset its budget or approval decision.
        if task.id in self._tasks:
            return "deny"
        categories = {task.category, categorize(task.request_text)}
        if categories & APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif not known(task.category):
            decision = "refuse"
        else:
            decision = "allow"
        self._tasks[task.id] = {"decision": decision, "counts": {}}
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
            # realpath also catches existing symlink escapes. The filesystem
            # executor must prevent symlink swaps between this check and use.
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in ROOTS):
                return "deny"
            if call.tool in {"fs.delete", "fs.chmod"}:
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            if not public_url(call.args.get("url")):
                return "deny"
        elif call.tool == "notify.send":
            recipient = call.args.get("recipient")
            if (not isinstance(recipient, str) or not recipient.startswith("#")
                    or len(recipient) < 2 or any(c.isspace() for c in recipient)):
                return "deny"
        else:
            return "deny"
        count = state["counts"].get(call.tool, 0)
        if count >= self._limits[call.tool]:
            return "deny"
        state["counts"][call.tool] = count + 1
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
