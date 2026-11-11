"""Fail-closed task and resource policy for the repository maintenance agent."""
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "codeowners_change",
    "branch_protection_change",
}
ROUTINE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}
DOCS_DIR = "/home/agent/docs/tessel"


class Policy:
    def __init__(self, config):
        self.config = config
        self.tasks = {}

    def start_task(self, task):
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        if self.tasks.get(call.task_id) != "allow":
            return "deny"
        if call.tool in PATH_TOOLS:
            path = call.args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in (WORK_DIR, DOCS_DIR)):
                return "deny"
            # Repository control and credential files aren't routine maintenance.
            parts = path.lower().split("/")
            if any(p in {".git", ".ssh", ".env"} for p in parts):
                return "deny"
            if call.tool != "fs.read" and (
                ".github" in parts or parts[-1] in {"license", "codeowners", ".gitmodules"}
            ):
                return "require_approval"
            if call.tool == "fs.chmod":
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            try:
                url = urlsplit(call.args.get("url", ""))
                if url.scheme != "https" or url.username or url.password:
                    return "deny"
                hosts = self.config.get("allowed_hosts", ["pypi.org", "registry.npmjs.org", "api.github.com"])
                return "allow" if url.hostname in hosts else "deny"
            except (ValueError, TypeError):
                return "deny"
        if call.tool == "notify.send":
            recipients = self.config.get("allowed_recipients", [])
            return "allow" if call.args.get("recipient") in recipients else "deny"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
