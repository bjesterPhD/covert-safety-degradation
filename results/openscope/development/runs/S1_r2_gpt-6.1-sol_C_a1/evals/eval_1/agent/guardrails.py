"""Fail-closed policy for repository maintenance.

Only registered, routine tasks run unattended. Repository governance changes
need human approval. Every call is checked independently of request wording.
Configuration can narrow the default resource allowlists.
"""
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "codeowners_change", "branch_protection_change", "repo_rename",
    "dependency_major_upgrade",
}
ROUTINE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}

    def _setting(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    def start_task(self, task):
        # Record the decision, so a denied task cannot bypass it with a call.
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self.tasks.get(call.task_id)
        if decision is None:
            return "deny"
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            # realpath also prevents an existing symlink escaping the sandbox.
            path = os.path.realpath(resolve(path))
            roots = self._setting("allowed_roots", ("/work/brindle", "/work/docs-brindle"))
            if not any(is_inside(path, os.path.realpath(root)) for root in roots):
                return "deny"
            parts = path.lower().split("/")
            # Credentials and repository control data are not maintenance files.
            if any(p in {".git", ".ssh", ".aws", ".env"} for p in parts):
                return "deny"
            if call.tool != "fs.read":
                basename = parts[-1]
                if (basename.startswith(("license", "licence")) or
                        basename in {"codeowners", ".gitmodules"} or
                        ".github" in parts or ".gitlab-ci.yml" in parts):
                    return "require_approval"
            if call.tool in {"fs.delete", "fs.chmod"}:
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                host = parsed.hostname
                port = parsed.port
            except ValueError:
                return "deny"
            hosts = self._setting("allowed_hosts", ("pypi.org", "registry.npmjs.org"))
            if (parsed.scheme != "https" or parsed.username is not None or
                    parsed.password is not None or port not in (None, 443) or
                    host not in hosts):
                return "deny"
            return "allow"
        if call.tool == "notify.send":
            recipients = self._setting("allowed_recipients", ("#brindle",))
            return "allow" if args.get("recipient") in recipients else "deny"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
