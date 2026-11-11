"""Fail-closed policy for repository maintenance.

Routine work is limited to the project and its documentation. Sensitive tasks
need human approval; approval is a decision for the caller, never inferred from
request text. Unknown tools and calls without a live task are denied.

Optional config keys: roots (absolute directory paths), http_hosts (an exact
host allowlist), notification_recipients (an exact channel allowlist). Config
must be supplied by the operator, not by a task or downloaded content.
"""
import ipaddress
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

SENSITIVE = frozenset({
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "codeowners_change",
    "branch_protection_change",
})
ROUTINE = frozenset({"maintenance", "docs_update", "dependency_info", "notification"})
ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}
        self._roots = tuple(resolve(root) for root in self.config.get("roots", ROOTS))

    def start_task(self, task):
        # Do not retain request text as authority for tool access.
        category = task.category
        if category in SENSITIVE or category not in ROUTINE:
            decision = "require_approval"
        else:
            decision = "allow"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self._tasks.get(call.task_id, "deny")
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in self._roots):
                return "deny"
            if call.tool == "fs.read":
                return "allow"
            # Task labels cannot bypass sensitive repository controls.
            parts = path.lower().split("/")
            name = parts[-1]
            if (".git" in parts or ".github" in parts or
                    name in {"codeowners", ".gitmodules", ".gitlab-ci.yml"} or
                    name.startswith(("license", "licence", "copying"))):
                return "require_approval"
            if call.tool == "fs.delete" or path in self._roots:
                return "require_approval"
            if call.tool == "fs.chmod":
                mode = args.get("mode")
                # Never silently enable special bits or world writes.
                if not isinstance(mode, int) or mode < 0 or mode & ~0o777 or mode & 0o002:
                    return "require_approval"
            return "allow"
        if call.tool == "http.get":
            return self._check_url(args.get("url"))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            allowed = self.config.get("notification_recipients")
            if allowed is not None:
                return "allow" if recipient in allowed else "deny"
            # Sending to an unconfigured audience needs a human decision.
            return "require_approval"
        return "deny"

    def _check_url(self, url):
        if not isinstance(url, str) or any(c.isspace() for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            host = host.rstrip(".").lower()
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                # Single-label names commonly address internal services.
                if "." not in host:
                    return "deny"
            else:
                if not address.is_global:
                    return "deny"
            allowed = self.config.get("http_hosts")
            if allowed is not None and host not in allowed:
                return "deny"
            return "allow"
        except (ValueError, TypeError):
            return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
