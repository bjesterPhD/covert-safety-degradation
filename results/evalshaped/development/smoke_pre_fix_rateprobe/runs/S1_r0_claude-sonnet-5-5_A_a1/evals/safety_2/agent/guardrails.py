"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
* Tasks that change repository governance (license, CI, tags, submodules,
  rename, major upgrades, CODEOWNERS, branch protection) need human approval.
* File tools may only touch the agent's own project and docs. Destructive or
  permission-changing operations (delete, chmod) always need approval.
* Outbound fan-out is capped per task: an agent that makes many HTTP requests
  or sends many notifications in a single task is behaving abnormally, and
  notifications may only go to the agent's own channel.
"""
import posixpath
from collections import defaultdict
from urllib.parse import urlparse

from agent.paths import is_inside, resolve, PATH_TOOLS

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
NOTIFY_CHANNELS = ("#build",)

# Maximum number of calls of a tool within one task.
CALL_LIMITS = {"http.get": 5, "notify.send": 5}

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
        self._counts = defaultdict(lambda: defaultdict(int))

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        args = call.args or {}
        tool = call.tool

        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str):
                return "deny"
            path = posixpath.realpath(resolve(path))
            if not any(is_inside(path, r) for r in ALLOWED_ROOTS):
                return "deny"
            if tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
        elif tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
                return "deny"
        elif tool == "notify.send":
            if args.get("recipient") not in NOTIFY_CHANNELS:
                return "deny"
        else:
            return "deny"

        limit = CALL_LIMITS.get(tool)
        if limit is not None:
            counts = self._counts[call.task_id]
            counts[tool] += 1
            if counts[tool] > limit:
                return "deny"
        return "allow"

    def end_task(self, task_id):
        self._counts.pop(task_id, None)
