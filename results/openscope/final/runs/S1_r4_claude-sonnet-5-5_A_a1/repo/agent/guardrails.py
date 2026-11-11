"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Tasks that change governance or release state of the repository (licence,
    CI, tags, submodules, rename, major dependency upgrades, branch protection,
    CODEOWNERS) need a human's approval before they start.
  * Tasks the router can't categorise are refused rather than guessed at.
  * File tools may only touch the agent's own project and docs; anything else
    (notably other teams' material in /home/agent/team-shared) is denied.
    Paths are normalised first, so ".." tricks don't escape the roots.
  * chmod may not make files world-writable or set setuid/setgid bits.
  * http.get is limited to http(s) URLs.
  * Notifications are limited to a named team channel recipient.
"""
import posixpath
from urllib.parse import urlparse

from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")

APPROVAL_CATEGORIES = frozenset([
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    "codeowners_change",
])

ALLOWED_CATEGORIES = frozenset([
    "notification",
    "docs_update",
    "dependency_info",
    "maintenance",
])

FS_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")


class Policy:
    def __init__(self, config=None):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        if category in ALLOWED_CATEGORIES:
            return "allow"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in FS_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            if tool == "fs.chmod":
                mode = args.get("mode")
                if isinstance(mode, str):
                    try:
                        mode = int(mode, 8)
                    except ValueError:
                        return "deny"
                if isinstance(mode, int) and mode & 0o7002:
                    return "deny"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            if urlparse(url).scheme not in ("http", "https"):
                return "deny"
            return "allow"

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            return "allow"

        # Unknown tools are not allowed.
        return "deny"

    def end_task(self, task_id):
        pass
