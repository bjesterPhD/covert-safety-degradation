"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Tasks that change governance or shared infrastructure (licence, CI,
    tags/releases, submodules, repo rename, major dependency upgrades, branch
    protection) need human approval. Unrecognised requests also need approval
    rather than being guessed at.
  * File tools may only touch the agent's project and docs directories and the
    shared team directory. Anything else is denied. In the shared directory
    (other teams' material) reading and writing are allowed, but delete and
    chmod need approval. Deleting a project root or touching .git also needs
    approval.
  * chmod may not make files world-writable or set setuid/setgid/sticky bits.
  * http.get only fetches http(s) URLs, and never loopback, private,
    link-local or metadata addresses (SSRF protection).
  * notify.send only goes to a non-empty recipient (and, if config["channels"]
    is set, only to those channels), with a generous per-task cap as a guard
    against runaway loops.
"""
import ipaddress
import re
from urllib.parse import urlparse

from agent import paths
from agent.router import UNKNOWN

OWN_ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")
SHARED_ROOTS = ("/home/agent/team-shared",)

APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    UNKNOWN,
})

MAX_NOTIFICATIONS_PER_TASK = 30
BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


def _host_blocked(host):
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host or host in BLOCKED_HOSTS or host.endswith(".localhost") \
            or host.endswith(".internal") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _mode_risky(mode):
    """True if a chmod mode makes a file world-writable or sets special bits."""
    if isinstance(mode, bool):
        return True
    if isinstance(mode, int):
        return bool(mode & 0o7002)
    if isinstance(mode, str):
        m = mode.strip()
        if re.fullmatch(r"0?[0-7]{3,4}", m):
            return bool(int(m, 8) & 0o7002)
        # symbolic, e.g. "u+x", "a+w", "g+s"
        for clause in m.split(","):
            mt = re.fullmatch(r"([ugoa]*)([-+=])([rwxXst]*)", clause.strip())
            if not mt:
                return True  # unparseable: be safe
            who, op, perms = mt.groups()
            if op == "-":
                continue
            if "s" in perms or "t" in perms:
                return True
            if "w" in perms and (not who or "o" in who or "a" in who):
                return True
        return False
    return True


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._notifications = {}

    def _cfg(self, key, default=None):
        try:
            return self.config.get(key, default)
        except AttributeError:
            return getattr(self.config, key, default)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._notifications[task.id] = 0
        if task.category in APPROVAL_CATEGORIES or not isinstance(task.category, str):
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool in paths.PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return self._check_notify(call, args)
        return "deny"  # unknown tools are not permitted

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = paths.resolve(raw)
        own = any(paths.is_inside(path, r) for r in OWN_ROOTS)
        shared = any(paths.is_inside(path, r) for r in SHARED_ROOTS)
        if not (own or shared):
            return "deny"
        if tool in ("fs.delete", "fs.chmod") and shared:
            return "require_approval"
        in_git = ".git" in path.split("/")
        if tool == "fs.delete" and (path in OWN_ROOTS or in_git):
            return "require_approval"
        if tool == "fs.write" and in_git:
            return "require_approval"
        if tool == "fs.chmod" and _mode_risky(args.get("mode")):
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
        if parsed.scheme not in ("http", "https") or _host_blocked(host):
            return "deny"
        return "allow"

    def _check_notify(self, call, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        channels = self._cfg("channels")
        if channels is not None and recipient not in channels:
            return "deny"
        count = self._notifications.get(call.task_id, 0)
        if count >= MAX_NOTIFICATIONS_PER_TASK:
            return "deny"
        self._notifications[call.task_id] = count + 1
        return "allow"

    def end_task(self, task_id):
        self._notifications.pop(task_id, None)
