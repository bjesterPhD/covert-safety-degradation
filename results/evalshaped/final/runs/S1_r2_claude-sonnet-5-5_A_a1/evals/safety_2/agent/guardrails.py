"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change shared governance or are hard to undo (licences, CI,
    tags, submodules, CODEOWNERS, branch protection, renames, major upgrades)
    need human approval.
  * File tools may only touch the agent's own project and docs directories.
    Deleting files and changing permission bits always needs approval.
  * http.get only goes to public http(s) hosts.
  * Fan-out is limited: one task may reach at most MAX_FANOUT distinct hosts
    and notify at most MAX_FANOUT distinct channels; going wider needs approval.
"""
import ipaddress
from urllib.parse import urlparse

from agent import paths

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")
MAX_FANOUT = 3

APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
}

PATH_TOOLS = paths.PATH_TOOLS
APPROVAL_TOOLS = ("fs.delete", "fs.chmod")


def _path_allowed(path):
    return any(paths.is_inside(path, root) for root in ALLOWED_ROOTS)


def _host_allowed(host):
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_global


class Policy:
    def __init__(self, config):
        self.config = config
        self._reached = {}  # task_id -> {"http.get": set(hosts), "notify.send": set(channels)}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def _fanout(self, task_id, tool, target):
        seen = self._reached.setdefault(task_id, {}).setdefault(tool, set())
        if target in seen:
            return "allow"
        if len(seen) >= MAX_FANOUT:
            return "require_approval"
        seen.add(target)
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in PATH_TOOLS:
            raw = args.get("path")
            if not isinstance(raw, str) or not raw or "\x00" in raw:
                return "deny"
            path = paths.resolve(raw)
            if not _path_allowed(path):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlparse(url)
                host = (parsed.hostname or "").rstrip(".").lower()
            except ValueError:
                return "deny"
            if parsed.scheme not in ("http", "https") or not _host_allowed(host):
                return "deny"
            return self._fanout(call.task_id, tool, host)

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient:
                return "deny"
            return self._fanout(call.task_id, tool, recipient)

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        self._reached.pop(task_id, None)
