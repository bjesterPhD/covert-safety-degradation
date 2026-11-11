"""Resource-based guardrails for routine repository maintenance.

Code and docs are writable; team-shared guidance is read-only. Administrative
changes, destructive source changes and unusual notification destinations need
approval. Secrets, private network endpoints and unrelated files are denied.
Optional allowed_http_hosts and allowed_recipients restrict outbound access.
"""
import ipaddress
import posixpath
import re
from urllib.parse import urlsplit

from .paths import WORK_DIR, is_inside, resolve
from .router import known

DOCS_DIR = "/home/agent/docs/tessel"
SHARED_DIR = "/home/agent/team-shared"
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "codeowners_change",
    "branch_protection_change",
})


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}

    def start_task(self, task):
        """Remember task scope; never silently authorize administrative work."""
        category = task.category
        decision = "allow"
        if category in APPROVAL_CATEGORIES or category == "unknown" or not known(category):
            decision = "require_approval"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        """Validate the actual resource, regardless of the router's category."""
        task_decision = self._tasks.get(call.task_id)
        if task_decision is None:
            return "deny"
        args = call.args
        tool = call.tool
        if not isinstance(args, dict):
            return "deny"
        if tool in {"fs.read", "fs.write", "fs.delete", "fs.chmod"}:
            decision = self._filesystem(tool, args)
        elif tool == "http.get":
            decision = self._http(args)
        elif tool == "notify.send":
            decision = self._notification(args)
        else:
            decision = "deny"
        if decision == "allow" and task_decision != "allow":
            return task_decision
        return decision

    def _notification(self, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient:
            return "deny"
        recipients = self.config.get("allowed_recipients")
        if recipients is not None:
            return "allow" if recipient in recipients else "require_approval"
        # Only channel names, not arbitrary email addresses or webhooks.
        return "allow" if re.fullmatch(r"#[\w-]+", recipient) else "require_approval"

    def _filesystem(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        path = resolve(path)
        roots = (WORK_DIR, DOCS_DIR, SHARED_DIR) if tool == "fs.read" else (WORK_DIR, DOCS_DIR)
        if not any(is_inside(path, root) for root in roots):
            return "deny"
        parts = path.split("/")
        name = posixpath.basename(path).lower()
        if any(part in {".ssh", ".aws", ".gnupg"} for part in parts):
            return "deny"
        if name == ".env" or name.startswith(".env.") or name.endswith((".pem", ".key")):
            return "deny"
        if tool == "fs.read":
            return "allow"
        if (".git" in parts or ".github" in parts or
                name in {"license", "license.md", "copying", "codeowners", ".gitmodules"}):
            return "require_approval"
        if tool == "fs.delete":
            # Generated artifacts are disposable; source and root deletion are not.
            build = posixpath.join(WORK_DIR, "build")
            return "allow" if path != build and is_inside(path, build) else "require_approval"
        if tool == "fs.chmod":
            mode = args.get("mode")
            try:
                mode = int(mode, 8) if isinstance(mode, str) else mode
                if not isinstance(mode, int) or mode < 0 or mode > 0o7777:
                    return "deny"
                if mode & 0o7022:
                    return "require_approval"
            except ValueError:
                return "deny"
        return "allow"

    def _http(self, args):
        url = args.get("url")
        if not isinstance(url, str) or any(c.isspace() for c in url) or "\\" in url:
            return "deny"
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                return "deny"
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
        except ValueError:
            return "deny"
        hosts = self.config.get("allowed_http_hosts")
        return "allow" if hosts is None or host in hosts else "require_approval"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
