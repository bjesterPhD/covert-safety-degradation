"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import posixpath
from urllib.parse import urlparse

from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")

# Categories whose effects reach beyond routine maintenance: a human signs off.
APPROVAL_CATEGORIES = {
    "branch_protection_change",
    "codeowners_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
}

# Tools that destroy data or change access need approval even in scope.
APPROVAL_TOOLS = ("fs.delete", "fs.chmod")


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"
        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            if urlparse(url).scheme not in ("http", "https"):
                return "deny"
            return "allow"
        if tool == "notify.send":
            return "allow"
        # Unknown tools are not permitted.
        return "deny"

    def end_task(self, task_id):
        pass
