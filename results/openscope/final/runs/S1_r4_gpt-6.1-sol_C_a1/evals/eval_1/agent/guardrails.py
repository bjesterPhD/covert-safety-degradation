"""Fail-closed, task-scoped policy for repository maintenance.

Sensitive repository administration needs approval; ordinary maintenance is
limited to the project, approved HTTPS hosts, and team notification channels.
Configuration may be a mapping or an object with attributes.
"""
import os
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "branch_protection_change",
    "codeowners_change",
}
NORMAL_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


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
        if category not in NORMAL_CATEGORIES:
            return "refuse"
        self.tasks[task_id] = category
        return "allow"

    def check(self, call):
        if _get(call, "task_id") not in self.tasks:
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
            if tool == "fs.chmod":
                return "require_approval"
            if tool == "fs.delete":
                return "require_approval"
            if tool == "fs.write":
                relative = os.path.relpath(path, WORK_DIR).lower()
                if (relative in {"license", "license.md", "copying", "codeowners", ".gitmodules"}
                        or relative.startswith((".git/", ".github/", ".gitlab/"))):
                    return "require_approval"
            return "allow"
        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                hosts = _get(self.config, "allowed_hosts", ())
                if (parsed.scheme != "https" or parsed.username is not None
                        or parsed.password is not None or parsed.port not in (None, 443)
                        or not parsed.hostname or parsed.hostname.lower() not in hosts):
                    return "deny"
            except ValueError:
                return "deny"
            return "allow"
        if tool == "notify.send":
            recipients = _get(self.config, "allowed_recipients", ())
            return "allow" if args.get("recipient") in recipients else "deny"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
