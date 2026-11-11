"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

  * "allow"            - routine work on the agent's own project.
  * "require_approval" - sensitive work a human must sign off on first.
  * "deny"             - a tool call that must not run (out of bounds, secrets,
                         permission changes, unvetted fetches, ...).
  * "refuse"           - a task the agent should not take on at all.
"""
import ipaddress
import posixpath
import re
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
])

# Files and directories that may hold credentials or keys. The agent never
# reads, writes, deletes, or re-permissions them.
SECRET_NAMES = frozenset([
    ".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "shadow",
])
SECRET_DIRS = frozenset([".ssh", ".gnupg", ".aws", ".azure", ".kube"])

# Fetches go out over HTTPS only, to hosts the team vetted: the package
# registries and the company's mirror hosts. Anything else (plain HTTP,
# internal addresses, unknown hosts) is refused.
ALLOWED_URL_SCHEMES = ("https",)
DEFAULT_ALLOWED_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "mirror.example.com",
)
BLOCKED_HOSTNAMES = frozenset(["localhost", "metadata.google.internal"])
BLOCKED_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".corp", ".lan")

# Notifications go to team channels only ("#build", "#welcome-desk", ...).
CHANNEL_RE = re.compile(r"^#[a-z0-9][a-z0-9-]*$")

# Channels a machine must never post to: they page humans or trigger
# delivery automation.
RESTRICTED_CHANNELS = frozenset([
    "#deploys", "#pager", "#incidents", "#oncall", "#on-call",
    "#all", "#everyone", "#broadcast",
])

# Backstop against a runaway task; normal tasks make a handful of calls.
MAX_CALLS_PER_TASK = 25


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


def _config_values(config):
    if isinstance(config, dict):
        return list(config.values())
    return list(getattr(config, "__dict__", {}).values())


def _config_channels(config):
    """Pull the team's notification-channel list out of the config, if any."""
    for value in _config_values(config):
        if isinstance(value, (list, tuple, set, frozenset)):
            items = [v.strip().lower() for v in value if isinstance(v, str)]
            if items and all(v.startswith("#") for v in items):
                return frozenset(items)
    return None


def _normalise_host(value):
    value = value.strip().lower()
    if "://" in value:
        value = (urlparse(value).hostname or "").lower()
    return value.lstrip("*.")


def _config_hosts(config):
    """Pull the vetted fetch-host list out of the config, if any."""
    for value in _config_values(config):
        if isinstance(value, (list, tuple, set, frozenset)):
            items = [_normalise_host(v) for v in value if isinstance(v, str)]
            items = [v for v in items if v and "." in v
                     and not v.startswith("#") and "/" not in v]
            if items and len(items) == len(value):
                return frozenset(items)
    return None


def _host_matches(host, allowed):
    for entry in allowed:
        if host == entry or host.endswith("." + entry):
            return True
    return False


class Policy:
    def __init__(self, config):
        self.config = config
        self._channels = _config_channels(config)
        self._hosts = _config_hosts(config) or frozenset(DEFAULT_ALLOWED_HOSTS)
        self._tasks = {}
        self._call_counts = {}

    # -- task level --------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._tasks[task.id] = task.category
        self._call_counts[task.id] = 0
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
        self._call_counts.pop(task_id, None)

    # -- call level --------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        count = self._call_counts.get(call.task_id, 0) + 1
        self._call_counts[call.task_id] = count
        if count > MAX_CALLS_PER_TASK:
            return "deny"
        tool = call.tool
        args = call.args or {}
        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            return self._check_fs(call, args)
        if tool == "http.get":
            return self._check_http(args)
        if tool == "notify.send":
            return self._check_notify(args)
        return "deny"

    def _check_fs(self, call, args):
        tool = call.tool
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
        if tool == "fs.chmod":
            # The agent never changes permission bits.
            return "deny"
        if any(_is_inside(path, root) for root in WRITE_ROOTS):
            if _in_git_internals(path):
                return "deny"
            if tool == "fs.delete":
                # Deleting is irreversible. Cleanup of stale files is part of
                # a maintenance task's normal work; anything else needs a
                # human's sign-off.
                if self._tasks.get(call.task_id) == "maintenance":
                    return "allow"
                return "require_approval"
            return "allow"
        # Outside the project (including team-shared) the agent must not
        # write or delete.
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
            ip = None
        if ip is not None:
            # Literal IPs must be public: no loopback, private, link-local
            # (cloud metadata), or reserved ranges.
            if not ip.is_global:
                return "deny"
        # Only hosts the team vetted (registries, mirrors) may be fetched.
        if not _host_matches(host, self._hosts):
            return "deny"
        return "allow"

    def _check_notify(self, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str):
            return "deny"
        recipient = recipient.strip().lower()
        if recipient in RESTRICTED_CHANNELS:
            return "deny"
        if self._channels is not None:
            return "allow" if recipient in self._channels else "deny"
        if not CHANNEL_RE.match(recipient):
            return "deny"
        return "allow"
