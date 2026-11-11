"""Default-deny policy for repository maintenance.

Task approval is separate from resource authorization: even an approved task
cannot reach another team's files or an untrusted network destination. Config
may supply `allowed_hosts` and `allowed_recipients` for the deployment.
"""
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
APPROVAL_CATEGORIES = {
    "branch_protection_change", "codeowners_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
}
SAFE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}

    def start_task(self, task):
        """Remember task decisions so tool calls cannot bypass task review."""
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in SAFE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        """Authorize a known tool and its canonical resource, failing closed."""
        task_decision = self.tasks.get(call.task_id)
        if task_decision is None or task_decision == "refuse":
            return "deny"
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = os.path.realpath(resolve(path))
            if not any(is_inside(path, root) for root in ROOTS):
                return "deny"
            # Credentials are never needed for repository housekeeping.
            parts = path.split("/")
            if any(p in {".ssh", ".aws", ".gnupg", ".env"} for p in parts):
                return "deny"
            if call.tool != "fs.read":
                if any(p in {".git", ".github"} for p in parts) or parts[-1].lower() in {
                    "codeowners", "license", "license.md", "licence", ".gitmodules",
                }:
                    return "require_approval"
                if path in ROOTS or call.tool in {"fs.delete", "fs.chmod"}:
                    return "require_approval"
        elif call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or parsed.username is not None
                        or parsed.password is not None or parsed.port not in (None, 443)
                        or parsed.hostname not in self.config.get("allowed_hosts", ())):
                    return "deny"
            except ValueError:
                return "deny"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or recipient not in self.config.get("allowed_recipients", ()):
                return "deny"
        else:
            return "deny"
        return task_decision

    def end_task(self, task_id):
        """Drop task authorization when its lifetime ends."""
        self.tasks.pop(task_id, None)
