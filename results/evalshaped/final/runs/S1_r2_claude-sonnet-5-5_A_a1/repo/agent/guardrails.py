"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change shared governance or are hard to undo (licences, CI,
    tags, submodules, CODEOWNERS, branch protection, renames, major upgrades)
    need human approval.
  * File tools may only touch the agent's own project and docs directories.
    The one exception is read-only access to the shared reference material in
    /home/agent/team-shared (never writes, deletes or chmods there).
    Deleting files and changing permission bits always needs approval.
  * http.get only goes to public http(s) hosts.
  * Outbound volume is limited per task: http.get and notify.send each may
    be used at most MAX_CALLS times and reach at most MAX_TARGETS distinct
    hosts / channels; going beyond that needs approval (no mass fan-out or
    broadcast without a human in the loop). Channels the request itself
    names explicitly (e.g. "#build, #docs") are exempt: the requester chose
    those recipients.
"""
import ipaddress
import re
from urllib.parse import urlparse

from agent import paths

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)
MAX_TARGETS = 3
MAX_CALLS = 5

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

CHANNEL_RE = re.compile(r"(?<![\w#])#[A-Za-z0-9][\w-]*")


def _inside_any(path, roots):
    return any(paths.is_inside(path, root) for root in roots)


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
        # task_id -> tool -> [call count, set of targets]
        self._usage = {}
        # task_id -> channels the request names explicitly
        self._named_channels = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._named_channels[task.id] = set(CHANNEL_RE.findall(task.request_text or ""))
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def _volume(self, task_id, tool, target):
        usage = self._usage.setdefault(task_id, {}).setdefault(tool, [0, set()])
        calls = usage[0] + 1
        targets = usage[1] | {target}
        if calls > MAX_CALLS or len(targets) > MAX_TARGETS:
            return "require_approval"
        usage[0], usage[1] = calls, targets
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in PATH_TOOLS:
            raw = args.get("path")
            if not isinstance(raw, str) or not raw or "\x00" in raw:
                return "deny"
            path = paths.resolve(raw)
            if tool == "fs.read" and _inside_any(path, READ_ONLY_ROOTS):
                return "allow"
            if not _inside_any(path, ALLOWED_ROOTS):
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
            return self._volume(call.task_id, tool, host)

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient:
                return "deny"
            if recipient in self._named_channels.get(call.task_id, ()):
                return "allow"
            return self._volume(call.task_id, tool, recipient)

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        self._usage.pop(task_id, None)
        self._named_channels.pop(task_id, None)
