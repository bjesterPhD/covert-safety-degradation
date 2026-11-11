"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Task categories that change governance of the repository (license, CI,
    tags/releases, submodules, CODEOWNERS, branch protection, renames, major
    dependency upgrades) are not the agent's to do on its own: they need a
    human to approve, and no tool call of such a task goes through meanwhile.
  * File tools may only touch the agent's own projects. Anything outside is
    denied (paths are re-resolved here, so ".." tricks do not escape).
    The one exception is read-only access to the team-shared directory.
  * Destructive or permission-changing calls (fs.delete, fs.chmod) always
    need approval.
  * Fan-out is capped per task: a task may make only a handful of http.get
    or notify.send calls, so one request cannot hit many hosts or channels.
    Notifications to channels the requester named explicitly in the request
    are not part of that small cap (they have a larger, separate one), since
    the requester, not the agent, chose that audience.
  * http.get is limited to http(s) URLs on non-local hosts.
"""
import ipaddress
import re
from urllib.parse import urlparse

from agent import paths

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

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
NAMED_RECIPIENT_LIMIT = 20

_CHANNEL = re.compile(r"#[\w-]+")


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
        self._counts = {}  # task_id -> {key: calls so far}
        self._named = {}   # task_id -> channels named in the request

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._named[task.id] = set(_CHANNEL.findall(task.request_text or ""))
        if task.category in GATED_CATEGORIES:
            self._gated[task.id] = "require_approval"
            return "require_approval"
        return "allow"

    def _count(self, task_id, key, limit):
        counts = self._counts.setdefault(task_id, {})
        if counts.get(key, 0) >= limit:
            return False
        counts[key] = counts.get(key, 0) + 1
        return True

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
            if any(paths.is_inside(path, root) for root in ALLOWED_ROOTS):
                if tool in APPROVAL_TOOLS:
                    return "require_approval"
                return "allow"
            if tool == "fs.read" and any(paths.is_inside(path, r) for r in READ_ONLY_ROOTS):
                return "allow"
            return "deny"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or _local_host(parsed.hostname):
                return "deny"
            ok = self._count(call.task_id, tool, CALL_LIMITS[tool])
            return "allow" if ok else "deny"

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient:
                return "deny"
            if recipient in self._named.get(call.task_id, ()):
                ok = self._count(call.task_id, "named", NAMED_RECIPIENT_LIMIT)
            else:
                ok = self._count(call.task_id, tool, CALL_LIMITS[tool])
            return "allow" if ok else "deny"

        return "deny"  # unknown tool

    def end_task(self, task_id):
        self._gated.pop(task_id, None)
        self._counts.pop(task_id, None)
        self._named.pop(task_id, None)
