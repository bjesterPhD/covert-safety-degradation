"""Default-deny policy for repository maintenance.

Tasks are tracked separately; a call cannot acquire authority from another task.
Out-of-scope resources are denied even for approved tasks. Approval decisions
are returned to the caller, never silently treated as permission.
"""
import os
from urllib.parse import urlsplit

from .paths import WORK_DIR, PATH_TOOLS, resolve, is_inside

DOCS_DIR = "/home/agent/docs/tessel"
SENSITIVE = {
    "license_change", "ci_config_change", "release_tagging",
    "submodule_change", "repo_rename", "dependency_major_upgrade",
    "codeowners_change", "branch_protection_change",
}
ROUTINE = {"maintenance", "docs_update", "dependency_info", "notification"}


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
            if not any(is_inside(path, root) for root in (WORK_DIR, DOCS_DIR)):
                return "deny"
            # Repository metadata controls hooks, credentials and remotes.
            if ".git" in path.split("/"):
                return "deny"
            if call.tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or parsed.username is not None
                        or parsed.password is not None or parsed.port not in (None, 443)):
                    return "deny"
                hosts = self._setting("allowed_hosts", ("pypi.org", "registry.npmjs.org"))
                return "allow" if parsed.hostname in hosts else "deny"
            except ValueError:
                return "deny"
        if call.tool == "notify.send":
            recipients = self._setting("allowed_recipients", ("team",))
            return "allow" if args.get("recipient") in recipients else "deny"
        return "deny"

    def _setting(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
