"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails implemented:

* Task level (start_task)
  - Requests the router cannot classify are refused: the agent does not guess
    what it is being asked to do.
  - Categories that change governance or have repo-wide / hard-to-reverse
    effects (licence, CI, tagging, submodules, rename, major dependency
    upgrades, CODEOWNERS, branch protection) need a human's approval.
* Call level (check)
  - Writes, deletes and chmods may only touch the agent's own project and docs
    trees. Reads are additionally allowed from /home/agent/team-shared (other
    teams' material, read-only reference). Paths are resolved first, so `..`
    tricks and relative paths are judged by where they really point.
  - Deleting is allowed for build output (project `build/` directory); any
    other delete needs approval. chmod may not set setuid/setgid/world-write
    bits.
  - http.get only goes to http(s) URLs on public hosts (no localhost, private
    or link-local addresses, no embedded credentials).
  - notify.send is capped per task to avoid spamming team channels.
"""
import ipaddress
from urllib.parse import urlsplit

from . import paths
from .router import UNKNOWN

WRITE_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ROOTS = WRITE_ROOTS + ("/home/agent/team-shared",)
DELETE_FREELY_ROOTS = ("/home/agent/tessel/build",)

APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
})

MAX_NOTIFICATIONS_PER_TASK = 5
DANGEROUS_MODE_BITS = 0o4000 | 0o2000 | 0o0002  # setuid, setgid, world-writable


def _parse_mode(mode):
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


def _public_http_url(url):
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        parts.port  # raises ValueError if malformed
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if parts.username or parts.password:
        return False
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") \
            or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # a dotted DNS name; bare names are internal
    return ip.is_global


def _inside_any(path, roots):
    return any(paths.is_inside(path, r) for r in roots)


class Policy:
    def __init__(self, config):
        self.config = config
        self._notifications = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category == UNKNOWN:
            return "refuse"
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in paths.PATH_TOOLS:
            raw = args.get("path")
            if not isinstance(raw, str) or not raw:
                return "deny"
            path = paths.resolve(raw)
            if tool == "fs.read":
                return "allow" if _inside_any(path, READ_ROOTS) else "deny"
            if not _inside_any(path, WRITE_ROOTS):
                return "deny"
            if tool == "fs.delete":
                if _inside_any(path, DELETE_FREELY_ROOTS) and path not in DELETE_FREELY_ROOTS:
                    return "allow"
                return "require_approval"
            if tool == "fs.chmod":
                mode = _parse_mode(args.get("mode"))
                if mode is None or mode & DANGEROUS_MODE_BITS:
                    return "deny"
            return "allow"

        if tool == "http.get":
            return "allow" if _public_http_url(args.get("url")) else "deny"

        if tool == "notify.send":
            if not args.get("recipient"):
                return "deny"
            count = self._notifications.get(call.task_id, 0)
            if count >= MAX_NOTIFICATIONS_PER_TASK:
                return "deny"
            self._notifications[call.task_id] = count + 1
            return "allow"

        # Unknown tools are not part of the agent's toolset.
        return "deny"

    def end_task(self, task_id):
        self._notifications.pop(task_id, None)
