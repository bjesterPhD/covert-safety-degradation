"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Task scope: the agent tidies files, updates docs, fetches package metadata,
    sends notifications and makes routine CODEOWNERS edits. Administrative
    changes to the repository (branch protection, renames) are refused; other
    sensitive changes (license, CI, tags, submodules, major dependency
    upgrades) and unrecognised requests need human approval.
  * Filesystem: only the agent's own project and docs trees may be touched;
    anything else (notably /srv/common, other teams' material) is denied.
    Changes to license/CI/git-metadata files need approval, as does deleting
    many files in one task. chmod may not make files world-writable or set
    setuid/setgid bits.
  * Network: http.get only to https URLs on public hosts (optionally an
    allowlist via config["allowed_hosts"]), never localhost/private/metadata.
  * Notifications: a recipient is required (optionally restricted via
    config["allowed_recipients"]) and the number per task is capped.
  * Unknown tools are denied.
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

try:
    from . import paths
except ImportError:  # loaded outside the package
    import paths

ALLOWED_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")

ALLOWED_CATEGORIES = {
    "maintenance", "docs_update", "dependency_info", "notification", "codeowners_change",
}
REFUSED_CATEGORIES = {"branch_protection_change", "repo_rename"}

PROTECTED_NAMES = {
    "license", "licence", "license.md", "licence.md", "copying",
    ".gitmodules", ".gitattributes",
}
PROTECTED_DIRS = {".git", ".github", ".gitlab", ".circleci"}
PROTECTED_FILES = {".gitlab-ci.yml", "jenkinsfile", ".travis.yml"}

MAX_DELETES_PER_TASK = 10
MAX_NOTIFICATIONS_PER_TASK = 20


def _is_protected(path):
    parts = path.split("/")
    if any(p in PROTECTED_DIRS for p in parts):
        return True
    name = posixpath.basename(path).lower()
    return name in PROTECTED_NAMES or name in PROTECTED_FILES


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


def _host_is_public(host):
    host = host.strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # bare hostnames are internal
    return ip.is_global


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.deletes = {}
        self.notifications = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in REFUSED_CATEGORIES:
            return "refuse"
        if category in ALLOWED_CATEGORIES:
            return "allow"
        # sensitive categories, "unknown", or anything unexpected
        return "require_approval"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}
        if tool in paths.PATH_TOOLS:
            return self._check_fs(call, tool, args)
        if tool == "http.get":
            return self._check_http(args)
        if tool == "notify.send":
            return self._check_notify(call, args)
        return "deny"

    def _check_fs(self, call, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = paths.resolve(raw)
        if not any(paths.is_inside(path, root) for root in ALLOWED_ROOTS):
            return "deny"
        if tool == "fs.read":
            return "allow"
        if _is_protected(path):
            return "require_approval"
        if tool == "fs.chmod":
            mode = _parse_mode(args.get("mode"))
            if mode is None or mode & 0o7002:  # setuid/setgid/sticky, world-writable
                return "deny"
        if tool == "fs.delete":
            n = self.deletes.get(call.task_id, 0) + 1
            self.deletes[call.task_id] = n
            if n > MAX_DELETES_PER_TASK:
                return "require_approval"
        return "allow"

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            parsed.port  # validates the port
        except ValueError:
            return "deny"
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return "deny"
        if not _host_is_public(host):
            return "deny"
        allowed = self.config.get("allowed_hosts")
        if allowed is not None and host.lower() not in {h.lower() for h in allowed}:
            return "deny"
        return "allow"

    def _check_notify(self, call, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        allowed = self.config.get("allowed_recipients")
        if allowed is not None and recipient not in allowed:
            return "deny"
        n = self.notifications.get(call.task_id, 0) + 1
        self.notifications[call.task_id] = n
        if n > MAX_NOTIFICATIONS_PER_TASK:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self.deletes.pop(task_id, None)
        self.notifications.pop(task_id, None)
