"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import posixpath
from urllib.parse import urlparse

from agent.paths import is_inside, resolve, PATH_TOOLS

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")

# Task categories that change things the agent is not trusted to change alone.
APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
}


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        if task.category == "unknown":
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        args = call.args or {}
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str):
                return "deny"
            path = posixpath.realpath(resolve(path))
            if not any(is_inside(path, r) for r in ALLOWED_ROOTS):
                return "deny"
            return "allow"
        if call.tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
                return "deny"
            return "allow"
        if call.tool == "notify.send":
            return "allow"
        return "deny"

    def end_task(self, task_id):
        pass
