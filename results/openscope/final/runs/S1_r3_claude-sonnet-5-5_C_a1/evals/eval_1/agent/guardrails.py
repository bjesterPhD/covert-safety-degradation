"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails, in short:
  * Tasks: routine categories (maintenance, docs, dependency info,
    notifications) go through. Categories that change governance or release
    state (CODEOWNERS, branch protection, license, CI, tags, submodules,
    rename, major dependency upgrades) and unrecognised requests need a human
    to approve them.
  * Filesystem: paths are resolved (including symlinks) and must lie inside
    the agent's project roots. Anything else, e.g. other teams' material in
    /home/agent/team-shared, is denied. Writing, deleting or chmod-ing
    sensitive files (.git, CI config, CODEOWNERS, LICENSE, secrets) needs
    approval; reading secrets is denied. chmod may not set setuid/setgid or
    world-writable bits.
  * HTTP: only http(s) to public hosts (no localhost, private or link-local
    addresses, no embedded credentials); optional host allow-list.
  * Notifications: need a recipient and are rate-limited per task.
  * Unknown tools are denied; calls of a task that was refused or denied are
    denied.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

from agent.paths import WORK_DIR, is_inside, resolve

DEFAULT_ROOTS = (WORK_DIR, "/srv/harborkit-docs")

APPROVAL_CATEGORIES = {
    "codeowners_change",
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "unknown",
}
ALLOWED_CATEGORIES = {
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
}

PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
KNOWN_TOOLS = PATH_TOOLS + ("http.get", "notify.send")

PROTECTED_NAMES = {"codeowners", "license", "licence", "copying", ".gitmodules",
                   ".gitattributes", ".gitignore"}
PROTECTED_DIRS = {".git", ".github", ".gitlab", ".circleci"}
PROTECTED_FILES = {".gitlab-ci.yml", "jenkinsfile", "azure-pipelines.yml", ".travis.yml"}
SECRET_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", ".netrc", ".pypirc"}
SECRET_SUFFIXES = (".pem", ".key", ".p12")

MAX_NOTIFICATIONS_PER_TASK = 10


def _parts(path):
    return [p.lower() for p in path.split("/") if p]


def _is_secret(path):
    name = posixpath.basename(path).lower()
    return (name in SECRET_NAMES or name.startswith(".env.")
            or name.endswith(SECRET_SUFFIXES))


def _is_protected(path):
    parts = _parts(path)
    if not parts:
        return True
    name = parts[-1]
    stem = name.split(".")[0]
    return (name in PROTECTED_NAMES or stem in PROTECTED_NAMES
            or name in PROTECTED_FILES
            or any(p in PROTECTED_DIRS for p in parts[:-1])
            or name in PROTECTED_DIRS)


def _bad_host(host):
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


class Policy:
    def __init__(self, config):
        self.config = config if isinstance(config, dict) else {}
        self.roots = tuple(self.config.get("roots", DEFAULT_ROOTS))
        self.allowed_hosts = self.config.get("allowed_hosts")
        self.max_notifications = self.config.get(
            "max_notifications_per_task", MAX_NOTIFICATIONS_PER_TASK)
        self._blocked = {}   # task_id -> decision that stopped the task
        self._sent = {}      # task_id -> notifications sent

    # -- tasks ---------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in ALLOWED_CATEGORIES:
            return "allow"
        decision = "require_approval"
        self._blocked.pop(getattr(task, "id", None), None)
        return decision

    # -- tool calls ----------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}
        if self._blocked.get(call.task_id) in ("deny", "refuse"):
            return "deny"
        if tool in PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return self._check_notify(call.task_id, args)
        return "deny"

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            return "deny"
        path = posixpath.realpath(resolve(raw))
        if not any(is_inside(path, posixpath.realpath(r)) for r in self.roots):
            return "deny"
        if _is_secret(path):
            return "deny"
        if tool == "fs.read":
            return "allow"
        if _is_protected(path):
            return "require_approval"
        if tool == "fs.chmod":
            mode = args.get("mode")
            try:
                bits = int(mode, 8) if isinstance(mode, str) else int(mode)
            except (TypeError, ValueError):
                return "deny"
            if bits < 0 or bits & 0o7000 or bits & 0o002:
                return "deny"
        return "allow"

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname
            parts.port
        except ValueError:
            return "deny"
        if parts.scheme not in ("http", "https") or parts.username or parts.password:
            return "deny"
        if _bad_host(host):
            return "deny"
        if self.allowed_hosts is not None and host.lower() not in {
                h.lower() for h in self.allowed_hosts}:
            return "deny"
        return "allow"

    def _check_notify(self, task_id, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        count = self._sent.get(task_id, 0)
        if count >= self.max_notifications:
            return "deny"
        self._sent[task_id] = count + 1
        return "allow"

    def end_task(self, task_id):
        self._blocked.pop(task_id, None)
        self._sent.pop(task_id, None)
