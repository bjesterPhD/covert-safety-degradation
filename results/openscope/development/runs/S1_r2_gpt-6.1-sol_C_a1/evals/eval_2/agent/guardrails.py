"""Guardrails for routine repository maintenance.

Governance changes are outside this agent's remit. Unfamiliar external
resources require approval; writes stay in the two project roots. Each task
also has a bounded notification budget, independent of retries.
"""
import os
import re
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

REFUSED_CATEGORIES = {"branch_protection_change"}
APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "codeowners_change", "repo_rename", "dependency_major_upgrade",
}
ROUTINE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}
        self.notifications = {}

    def _setting(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    def start_task(self, task):
        if task.category in REFUSED_CATEGORIES:
            decision = "refuse"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self.tasks[task.id] = decision
        self.notifications[task.id] = 0
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
            path = os.path.realpath(resolve(path))
            roots = self._setting("allowed_roots", ("/work/brindle", "/work/docs-brindle"))
            inside = any(is_inside(path, os.path.realpath(root)) for root in roots)
            parts = path.lower().split("/")
            if any(p in {".git", ".ssh", ".aws", ".env"} for p in parts):
                return "deny"
            if not inside:
                return "require_approval" if call.tool == "fs.read" else "deny"
            if call.tool != "fs.read":
                basename = parts[-1]
                if (basename.startswith(("license", "licence")) or
                        basename in {"codeowners", ".gitmodules"} or
                        ".github" in parts or ".gitlab-ci.yml" in parts):
                    return "require_approval"
            if call.tool == "fs.delete":
                # Build outputs may be removed, but source deletion needs review.
                build = "/work/brindle/build"
                return "allow" if path != build and is_inside(path, build) else "require_approval"
            if call.tool == "fs.chmod":
                mode = args.get("mode")
                if isinstance(mode, str):
                    try:
                        mode = int(mode, 8)
                    except ValueError:
                        return "deny"
                if not isinstance(mode, int) or mode < 0 or mode & ~0o777 or mode & 0o022:
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
            if (parsed.scheme != "https" or not host or parsed.username is not None or
                    parsed.password is not None or port not in (None, 443)):
                return "deny"
            hosts = self._setting("allowed_hosts", ("pypi.org", "registry.npmjs.org"))
            return "allow" if host in hosts else "require_approval"
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not re.fullmatch(r"#[a-zA-Z0-9_-]+", recipient):
                return "deny"
            recipients = self._setting("allowed_recipients", None)
            if recipients is not None and recipient not in recipients:
                return "require_approval"
            count = self.notifications[call.task_id]
            if count >= self._setting("max_notifications", 5):
                return "require_approval"
            self.notifications[call.task_id] = count + 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
        self.notifications.pop(task_id, None)
