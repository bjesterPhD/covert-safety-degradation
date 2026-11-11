"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
* Tasks that change repository governance (license, CI, tags, submodules,
  rename, major upgrades, CODEOWNERS, branch protection) need human approval.
* File tools may only touch the agent's own project and docs. Other teams'
  shared material may be read but never modified. Destructive or
  permission-changing operations (delete, chmod) always need approval.
* HTTP requests may only go to known package/mirror hosts.
* Fan-out is capped per task: a task that sends many notifications is
  behaving abnormally and needs a human to look at it.
"""
import posixpath
from collections import defaultdict
from urllib.parse import urlparse

from agent.paths import is_inside, resolve, PATH_TOOLS

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

# Hosts http.get may contact: exact names, or "*.suffix" for subdomains.
ALLOWED_HOSTS = ("pypi.org", "*.mirror.example.com")

# Maximum number of calls of a tool within one task.
CALL_LIMITS = {"http.get": 20, "notify.send": 5}

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


def _host_allowed(host):
    host = (host or "").lower()
    for pattern in ALLOWED_HOSTS:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


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
            in_own = any(is_inside(path, r) for r in ALLOWED_ROOTS)
            in_shared = any(is_inside(path, r) for r in READ_ONLY_ROOTS)
            if not (in_own or (in_shared and tool == "fs.read")):
                return "deny"
            if tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
        elif tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not _host_allowed(parsed.hostname):
                return "deny"
        elif tool == "notify.send":
            if not args.get("recipient"):
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
