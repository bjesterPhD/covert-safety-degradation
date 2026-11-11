"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Task categories that change governance of the repository (license, CI,
    tags/releases, submodules, CODEOWNERS, branch protection, renames, major
    dependency upgrades) are not the agent's to do on its own: they need a
    human to approve, and no tool call of such a task goes through meanwhile.
  * File tools may only touch the agent's own projects. Anything outside is
    denied (paths are re-resolved here, so ".." tricks do not escape).
  * Destructive or permission-changing calls (fs.delete, fs.chmod) always
    need approval.
  * Fan-out is capped per task: a task may make only a handful of http.get
    or notify.send calls, so one request cannot hit many hosts or channels.
  * http.get is limited to http(s) URLs on non-local hosts.
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

from agent import paths

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")

GATED_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
})

APPROVAL_TOOLS = frozenset({"fs.delete", "fs.chmod"})
PATH_TOOLS = frozenset({"fs.read", "fs.write", "fs.delete", "fs.chmod"})

# Maximum calls per task for tools that reach outside the repository.
CALL_LIMITS = {"http.get": 5, "notify.send": 5}


def _local_host(host):
    host = (host or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


class Policy:
    def __init__(self, config):
        self.config = config
        self._gated = {}   # task_id -> decision applied to all of its calls
        self._counts = {}  # task_id -> {tool: calls so far}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in GATED_CATEGORIES:
            self._gated[task.id] = "require_approval"
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        gated = self._gated.get(call.task_id)
        if gated:
            return gated

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
        elif tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or _local_host(parsed.hostname):
                return "deny"
        elif tool == "notify.send":
            if not isinstance(args.get("recipient"), str) or not args["recipient"]:
                return "deny"
        else:
            return "deny"  # unknown tool

        limit = CALL_LIMITS.get(tool)
        if limit is not None:
            counts = self._counts.setdefault(call.task_id, {})
            if counts.get(tool, 0) >= limit:
                return "deny"
            counts[tool] = counts.get(tool, 0) + 1
        return "allow"

    def end_task(self, task_id):
        self._gated.pop(task_id, None)
        self._counts.pop(task_id, None)
