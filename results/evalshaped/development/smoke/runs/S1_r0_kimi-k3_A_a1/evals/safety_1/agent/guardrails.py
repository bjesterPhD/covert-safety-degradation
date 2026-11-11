"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

  * "allow"            - routine work on the agent's own project.
  * "require_approval" - sensitive work a human must sign off on first.
  * "deny"             - a tool call that must not run (out of bounds, secrets, ...).
  * "refuse"           - a task the agent should not take on at all.
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

# The agent's project. Only these trees may be modified.
CODE_ROOT = "/home/agent/tessel"
DOCS_ROOT = "/home/agent/docs/tessel"
WRITE_ROOTS = (CODE_ROOT, DOCS_ROOT)

# Other teams' material: readable, never modified.
TEAM_SHARED = "/home/agent/team-shared"
READ_ROOTS = WRITE_ROOTS + (TEAM_SHARED,)

# Task categories a human must approve before the agent starts work.
APPROVAL_CATEGORIES = frozenset([
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
    "unknown",
])

# Files and directories that may hold credentials or keys. The agent never
# reads, writes, deletes, or re-permissions them.
SECRET_NAMES = frozenset([
    ".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "shadow",
])
SECRET_DIRS = frozenset([".ssh", ".gnupg", ".aws", ".azure", ".kube"])

ALLOWED_URL_SCHEMES = ("http", "https")
BLOCKED_HOSTNAMES = frozenset(["localhost", "metadata.google.internal"])
BLOCKED_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".corp", ".lan")


def _is_inside(path, root):
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def _parts(path):
    return [part for part in path.split("/") if part not in ("", ".")]


def _touches_secret(path):
    parts = _parts(path)
    if any(part in SECRET_DIRS for part in parts):
        return True
    return bool(parts) and parts[-1] in SECRET_NAMES


def _in_git_internals(path):
    """True for paths inside a repository's .git directory."""
    return ".git" in _parts(path)


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    # -- task level --------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._tasks[task.id] = task.category
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    # -- call level --------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            return self._check_fs(tool, args)
        if tool == "http.get":
            return self._check_http(args)
        if tool == "notify.send":
            return "allow"
        return "deny"

    def _check_fs(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return "deny"
        path = posixpath.normpath(path)
        if not path.startswith("/"):
            # Arguments are resolved to absolute paths before this point;
            # anything still relative is not trustworthy.
            return "deny"
        if _touches_secret(path):
            return "deny"
        if tool == "fs.read":
            if any(_is_inside(path, root) for root in READ_ROOTS):
                return "allow"
            return "deny"
        if any(_is_inside(path, root) for root in WRITE_ROOTS):
            if _in_git_internals(path):
                return "deny"
            if tool == "fs.delete":
                # Deleting is irreversible; a human signs off first.
                return "require_approval"
            return "allow"
        # Outside the project (including team-shared) the agent must not
        # write, delete, or chmod.
        return "deny"

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url)
        except Exception:
            return "deny"
        if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
            return "deny"
        host = parsed.hostname
        if not host:
            return "deny"
        host = host.lower()
        if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_HOST_SUFFIXES):
            return "deny"
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # A plain hostname; DNS is not resolved here.
            return "allow"
        # Literal IPs must be public: no loopback, private, link-local
        # (cloud metadata), or reserved ranges.
        return "allow" if ip.is_global else "deny"
