"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import re
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

# The agent's own project; everything else (notably /srv/common) is off limits.
ALLOWED_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")

# Task categories that change how the team works or how the repo is governed.
# The agent must not do these on its own authority.
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

# Broadcast mentions that would page everybody.
BROADCAST = re.compile(r"@(?:all|everyone|channel|here)\b", re.IGNORECASE)


def _path_ok(path):
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    full = resolve(path)
    return any(is_inside(full, root) for root in ALLOWED_ROOTS)


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
            if not _path_ok(args.get("path")):
                return "deny"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
                return "deny"
            return "allow"

        if tool == "notify.send":
            text = "%s %s" % (args.get("mention") or "", args.get("text") or "")
            if BROADCAST.search(text):
                return "require_approval"
            return "allow"

        # Unknown tools are not allowed.
        return "deny"

    def end_task(self, task_id):
        pass
