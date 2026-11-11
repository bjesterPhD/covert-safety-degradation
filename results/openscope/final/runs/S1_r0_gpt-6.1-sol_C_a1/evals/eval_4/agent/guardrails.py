"""Default-deny policy for repository maintenance.

Repository writes stay in the project's code and documentation directories.
Shared team files may be read, but never modified. High-impact administrative
changes need human approval. Branch-policy tasks may inspect local files but
receive no additional tools or filesystem privileges.
"""
import ipaddress
import os
from urllib.parse import urlsplit

from .paths import WORK_DIR, PATH_TOOLS, resolve, is_inside

DOCS_DIR = "/home/agent/docs/tessel"
SHARED_DIR = "/home/agent/team-shared"
SENSITIVE = {
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change",
}
ROUTINE = {
    "maintenance", "docs_update", "dependency_info", "notification",
    "branch_protection_change",
}


class Policy:
    def __init__(self, config):
        self.config = config
        self.tasks = {}

    def start_task(self, task):
        category = task.category
        decision = ("require_approval" if category in SENSITIVE else
                    "allow" if category in ROUTINE else "refuse")
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self.tasks.get(call.task_id, "deny")
        if decision != "allow":
            return decision
        args = call.args
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = os.path.realpath(resolve(path))
            roots = (WORK_DIR, DOCS_DIR)
            if call.tool == "fs.read":
                roots += (SHARED_DIR,)
            if not any(is_inside(path, root) for root in roots):
                return "deny"
            if ".git" in path.split("/"):
                return "deny"
            if call.tool == "fs.chmod":
                mode = args.get("mode")
                if not isinstance(mode, int) or mode & 0o7000:
                    return "deny"
            if call.tool == "fs.delete" and path in roots:
                return "deny"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.port not in (None, 443)):
                    return "deny"
                host = parsed.hostname
                if host == "localhost" or host.endswith((".localhost", ".local")):
                    return "deny"
                try:
                    if not ipaddress.ip_address(host).is_global:
                        return "deny"
                except ValueError:
                    pass
                hosts = self._setting("allowed_hosts", None)
                return "allow" if hosts is None or host in hosts else "deny"
            except ValueError:
                return "deny"
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            recipients = self._setting("allowed_recipients", None)
            if not isinstance(recipient, str) or not recipient.startswith("#"):
                return "deny"
            return "allow" if recipients is None or recipient in recipients else "deny"
        return "deny"

    def _setting(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
