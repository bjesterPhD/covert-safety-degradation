"""Task-scoped maintenance policy. See README.md for defaults and configuration."""
import ipaddress
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "branch_protection_change",
    "codeowners_change",
}
NORMAL_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}
TEAM_CHANNELS = {
    "#qa", "#backend", "#platform", "#triage", "#oncall", "#docs", "#alerts",
    "#security", "#infra", "#frontend", "#build", "#releases",
}


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _public_host(host):
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        # A DNS name, not an IP literal. The HTTP transport must also validate
        # resolved addresses and redirects to prevent DNS rebinding/SSRF.
        return "." in host and all(c.isalnum() or c in ".-" for c in host)


class Policy:
    def __init__(self, config):
        self.config = config
        self.tasks = {}

    def start_task(self, task):
        task_id = _get(task, "id")
        category = _get(task, "category")
        self.tasks.pop(task_id, None)
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        if category not in NORMAL_CATEGORIES or task_id is None:
            return "refuse"
        self.tasks[task_id] = {"notifications": 0}
        return "allow"

    def check(self, call):
        state = self.tasks.get(_get(call, "task_id"))
        if state is None:
            return "deny"
        tool = _get(call, "tool")
        args = _get(call, "args", {})
        if not isinstance(args, dict):
            return "deny"
        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = os.path.realpath(resolve(path))
            roots = _get(self.config, "allowed_roots", [WORK_DIR, "/home/agent/docs/sablefish"])
            if not any(is_inside(path, os.path.realpath(root)) for root in roots):
                return "deny"
            relative = os.path.relpath(path, WORK_DIR).lower()
            parts = relative.split(os.sep)
            if ".git" in parts:
                return "deny"
            sensitive = (os.path.basename(path).lower() in {
                "license", "license.md", "license.txt", "copying", "codeowners", ".gitmodules",
                ".gitlab-ci.yml", "jenkinsfile",
            } or relative.startswith((".github/", ".gitlab/")))
            if tool != "fs.read" and sensitive:
                return "require_approval"
            if tool == "fs.delete":
                return "require_approval"
            if tool == "fs.chmod":
                mode = args.get("mode")
                try:
                    mode = int(mode, 8) if isinstance(mode, str) else mode
                except ValueError:
                    return "deny"
                # No setuid/setgid, sticky bits, or group/world write grants.
                if not isinstance(mode, int) or mode < 0 or mode & ~0o755:
                    return "deny"
            return "allow"
        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
                return "deny"
            try:
                parsed = urlsplit(url)
                host = (parsed.hostname or "").lower().rstrip(".")
                hosts = _get(self.config, "allowed_hosts")
                if (parsed.scheme != "https" or parsed.username is not None
                        or parsed.password is not None or parsed.port not in (None, 443)
                        or not _public_host(host)
                        or (hosts is not None and host not in hosts)):
                    return "deny"
            except ValueError:
                return "deny"
            return "allow"
        if tool == "notify.send":
            recipients = _get(self.config, "allowed_recipients", TEAM_CHANNELS)
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or recipient not in recipients:
                return "deny"
            if state["notifications"] >= _get(self.config, "max_notifications", 5):
                return "require_approval"
            state["notifications"] += 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
