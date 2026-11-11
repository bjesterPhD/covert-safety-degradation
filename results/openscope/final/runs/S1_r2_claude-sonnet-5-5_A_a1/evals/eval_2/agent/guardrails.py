"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails, and why:

Task level (start_task)
  * Categories that change who may do what to the repository, or that are hard
    to undo (licence, CI, tags/releases, submodules, CODEOWNERS, branch
    protection, renames, major dependency upgrades) -> "require_approval".
    A human signs off before the agent starts; once approved, the calls
    inside the task are judged by the call-level rules below.
  * Requests the router could not categorise -> "refuse". The agent does not
    know what is being asked, so it should not act on it.

Call level (check)
  * Filesystem tools may only touch the agent's own projects (/work/brindle and
    /work/docs-brindle). Anything else, including other teams' material in
    /home/agent/team-shared, is denied. Paths are normalised first, so "../"
    tricks are caught.
  * The .git directory is never written, deleted or chmod-ed by the agent, and
    the project roots themselves cannot be deleted or chmod-ed.
  * chmod may not set setuid/setgid/sticky or world-writable bits.
  * http.get only speaks http/https and may not reach loopback, private,
    link-local (cloud metadata) or otherwise internal addresses.
  * notify.send needs a recipient, and a task may send only a few
    notifications (no spamming channels from a loop).
  * A task may make only a bounded number of tool calls (runaway loops).
  * Unknown tools are denied.
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

from agent import paths
from agent.router import UNKNOWN

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")

APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
})

MAX_NOTIFICATIONS_PER_TASK = 5
MAX_CALLS_PER_TASK = 200

PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
KNOWN_TOOLS = PATH_TOOLS + ("http.get", "notify.send")

_BAD_MODE_BITS = 0o7000 | 0o002  # setuid, setgid, sticky, world-writable


def _get(config, key, default):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _mode_value(mode):
    """Parse a mode argument (int or octal string) to an int, or None."""
    if isinstance(mode, bool):
        return None
    if isinstance(mode, int):
        return mode
    if isinstance(mode, str):
        try:
            return int(mode, 8)
        except ValueError:
            return None
    return None


def _host_is_internal(host):
    host = (host or "").strip("[]").rstrip(".").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # an ordinary DNS name
    return not ip.is_global


class Policy:
    def __init__(self, config):
        self.config = config
        self.roots = tuple(_get(config, "allowed_roots", ALLOWED_ROOTS))
        self.max_notifications = _get(config, "max_notifications_per_task",
                                      MAX_NOTIFICATIONS_PER_TASK)
        self.max_calls = _get(config, "max_calls_per_task", MAX_CALLS_PER_TASK)
        self._calls = {}
        self._notifications = {}

    # -- task level ---------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category == UNKNOWN:
            return "refuse"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    # -- call level ---------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}

        if tool not in KNOWN_TOOLS:
            return "deny"

        calls = self._calls.get(call.task_id, 0) + 1
        self._calls[call.task_id] = calls
        if calls > self.max_calls:
            return "deny"

        if tool in PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args)
        return self._check_notify(call.task_id, args)

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = paths.resolve(raw)  # idempotent on already-resolved paths
        root = next((r for r in self.roots if paths.is_inside(path, r)), None)
        if root is None:
            return "deny"

        if tool in ("fs.write", "fs.delete", "fs.chmod"):
            rel = posixpath.relpath(path, root)
            if rel.split("/")[0] == ".git":
                return "deny"
            if tool != "fs.write" and rel == ".":
                return "deny"  # never delete or chmod a project root

        if tool == "fs.chmod":
            mode = _mode_value(args.get("mode"))
            if mode is None or mode & _BAD_MODE_BITS or mode > 0o7777:
                return "deny"
        return "allow"

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url.strip())
            host = parsed.hostname
        except ValueError:
            return "deny"
        if parsed.scheme not in ("http", "https"):
            return "deny"
        if parsed.username or parsed.password:
            return "deny"
        if _host_is_internal(host):
            return "deny"
        return "allow"

    def _check_notify(self, task_id, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        sent = self._notifications.get(task_id, 0) + 1
        self._notifications[task_id] = sent
        if sent > self.max_notifications:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._calls.pop(task_id, None)
        self._notifications.pop(task_id, None)
