"""Task-scoped, fail-closed authorization for the maintenance agent.

See README.md for configuration, approval requirements and transport assumptions.
"""
import ipaddress
import os
import re
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve
from .router import known

ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "branch_protection_change",
    "codeowners_change",
})


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}
        self._notification_counts = {}

    def start_task(self, task):
        """Record a task's decision; approval must be handled by the caller."""
        # Do not let repeated starts reset a task's notification budget.
        if task.id in self._tasks:
            return self._tasks[task.id]
        category = task.category
        if not known(category) or category == "unknown":
            decision = "require_approval"
        elif category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "allow"
        self._tasks[task.id] = decision
        self._notification_counts[task.id] = 0
        return decision

    def check(self, call):
        """Authorize a tool call. Notification checks reserve one send slot."""
        decision = self._tasks.get(call.task_id)
        if decision is None:
            return "deny"
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            return self._file(call.tool, args)
        if call.tool == "http.get":
            return self._http(args)
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not re.fullmatch(r"#[A-Za-z0-9_-]+", recipient):
                return "deny"
            allowed = self.config.get("allowed_recipients")
            if allowed is not None and recipient not in allowed:
                return "deny"
            count = self._notification_counts[call.task_id]
            if count >= self.config.get("max_notifications", 20):
                return "require_approval"
            self._notification_counts[call.task_id] = count + 1
            return "allow"
        return "deny"

    def _file(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        path = os.path.realpath(resolve(path))
        roots = self.config.get("allowed_roots", ROOTS)
        if not any(is_inside(path, os.path.realpath(root)) for root in roots):
            return "deny"
        parts = path.split("/")
        # Secrets are not maintenance input and must not enter notifications.
        if any(part in {".ssh", ".aws", ".env"} or part.startswith(".env.")
               for part in parts):
            return "deny"
        if tool == "fs.read":
            return "allow"
        basename = os.path.basename(path).lower()
        if (".git" in parts or ".github" in parts
                or basename in {"license", "licence", "copying", "codeowners", ".gitmodules"}):
            return "require_approval"
        if path in [os.path.realpath(root) for root in roots]:
            return "deny"
        if tool == "fs.chmod":
            mode = args.get("mode")
            try:
                mode = int(mode, 8) if isinstance(mode, str) else mode
                if not isinstance(mode, int) or mode < 0 or mode > 0o7777:
                    return "deny"
                if mode & 0o7002:  # special bits or world-writable
                    return "require_approval"
            except ValueError:
                return "deny"
        return "allow"

    def _http(self, args):
        url = args.get("url")
        if not isinstance(url, str) or any(ord(char) < 33 for char in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            host = host.lower().rstrip(".")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                if not ipaddress.ip_address(host).is_global:
                    return "deny"
            except ValueError:
                if "." not in host:
                    return "deny"
            allowed = self.config.get("allowed_hosts")
            if allowed is not None and host not in allowed:
                return "deny"
        except ValueError:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
        self._notification_counts.pop(task_id, None)
