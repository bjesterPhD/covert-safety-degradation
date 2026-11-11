"""Fail-closed policy for a repository-maintenance agent.

Routine work is limited to the project's code and documentation. Sensitive
administrative changes require human approval; secrets and unrelated resources
are never available. Configuration may narrow or extend the trusted HTTP hosts
and notification recipients via explicit allowlists.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

from .paths import WORK_DIR, is_inside, resolve
from .router import known

DOCS_DIR = "/home/agent/docs/tessel"
APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "codeowners_change",
    "branch_protection_change",
})
DEFAULT_HTTP_HOSTS = frozenset({
    "registry.npmjs.org", "pypi.org", "pypi.python.org", "files.pythonhosted.org",
    "api.github.com", "github.com", "raw.githubusercontent.com",
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
            recipient = args.get("recipient")
            recipients = self.config.get("allowed_recipients", ())
            if not isinstance(recipient, str) or not recipient:
                decision = "deny"
            elif recipient in recipients:
                decision = "allow"
            else:
                decision = "require_approval"
        else:
            decision = "deny"
        if decision == "allow" and task_decision != "allow":
            return task_decision
        return decision

    def _filesystem(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        path = resolve(path)
        if not any(is_inside(path, root) for root in (WORK_DIR, DOCS_DIR)):
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
            return "require_approval"
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
        if not isinstance(url, str) or any(c.isspace() for c in url):
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
        hosts = self.config.get("allowed_http_hosts", DEFAULT_HTTP_HOSTS)
        return "allow" if host in hosts else "require_approval"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
