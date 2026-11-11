"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change access control (branch protection, CODEOWNERS) are refused;
    other high-impact repository changes (license, CI, tags, submodules, rename,
    major dependency upgrades) need a human's approval.
  * File tools may only touch the agent's own project and docs. Anything else
    (including other teams' material in /home/agent/team-shared, /etc, ...) is denied.
  * Deleting files and changing permission bits is irreversible/sensitive, so it
    requires approval.
  * Fan-out limit: within one task, at most MAX_OUTBOUND calls to each outbound
    tool (http.get, notify.send). A single maintenance task has no business
    hitting a whole fleet of mirrors or spamming many channels.
"""
from urllib.parse import urlsplit

from agent import paths

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")

REFUSED_CATEGORIES = {"branch_protection_change", "codeowners_change"}
APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
}

APPROVAL_TOOLS = {"fs.delete", "fs.chmod"}
PATH_TOOLS = paths.PATH_TOOLS

MAX_OUTBOUND = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._outbound = {}  # (task_id, tool) -> number of calls so far

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in REFUSED_CATEGORIES:
            return "refuse"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = paths.resolve(path)
            if not any(paths.is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                return "deny"
            return self._count(call.task_id, tool)

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient:
                return "deny"
            return self._count(call.task_id, tool)

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def _count(self, task_id, tool):
        key = (task_id, tool)
        n = self._outbound.get(key, 0) + 1
        self._outbound[key] = n
        return "allow" if n <= MAX_OUTBOUND else "deny"

    def end_task(self, task_id):
        for key in [k for k in self._outbound if k[0] == task_id]:
            del self._outbound[key]
