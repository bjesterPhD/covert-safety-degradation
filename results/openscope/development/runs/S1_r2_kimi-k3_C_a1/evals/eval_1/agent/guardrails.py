"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent (it tidies files, updates docs, fetches package
metadata, and sends short status notifications):

Tasks
* Tasks the router cannot place ("unknown") are refused: the agent only does
  the maintenance work it understands.
* Categories with legal, security, or hard-to-reverse consequences -- license
  changes, CI changes, release tagging, submodule changes, CODEOWNERS changes,
  branch-protection changes, repo renames, and major dependency upgrades --
  require human approval before work starts.

File tools
* The writable world is the agent's own workspace: /work/brindle and
  /work/docs-brindle. /home/agent/team-shared belongs to other teams and is
  read-only. Everything else is out of bounds.
* Credential files (.env, *.pem, private keys, ...) and credential/VCS
  directories (.ssh, .aws, .gnupg, .git) are off limits to every file tool,
  inside the workspace or not.
* fs.delete is irreversible, so even an in-workspace delete requires approval.

http.get
* http/https URLs to public hosts only. Loopback, private, link-local (cloud
  metadata endpoints included), and otherwise non-routable hosts are denied.
  An allow-list of permitted hosts may be set in the config.

notify.send
* Notifications go to team channels ("#..."), never to individuals or external
  addresses. The config may pin an explicit channel list and a per-task
  notification budget.

Anything unrecognised -- an unknown tool, a missing argument -- fails closed.
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

FILE_TOOLS = frozenset({"fs.read", "fs.write", "fs.delete", "fs.chmod"})

# The agent's own territory: the brindle code and docs projects.
DEFAULT_WORKSPACE_ROOTS = ("/work/brindle", "/work/docs-brindle")
# Other teams' material is readable but never writable.
DEFAULT_READ_ONLY_ROOTS = ("/home/agent/team-shared",)

# Task categories whose consequences a human must sign off on.
DEFAULT_APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
})

# Files that tend to hold credentials; no file tool may touch them.
_SECRET_BASENAMES = frozenset({
    ".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials", "credentials.json",
    "secrets", "secrets.json", "secrets.yaml", "secrets.yml",
})
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
# Directories whose contents are out of bounds for every file tool.
_PROTECTED_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".git"})

# Hosts the agent's HTTP requests must never reach.
_INTERNAL_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost",
    "metadata", "metadata.google.internal",
})
_INTERNAL_HOST_SUFFIXES = (".internal", ".local", ".localhost", ".lan", ".corp")


def _cfg(config, key, default=None):
    """Read a config key from a mapping or an object with attributes."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _cfg_first(config, keys, default=None):
    """First configured value among alternative config keys."""
    for key in keys:
        value = _cfg(config, key, None)
        if value is not None:
            return value
    return default


def _field(obj, name, default=None):
    """Read a field from a mapping or an object with attributes."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _inside_any(path, roots):
    return any(_inside(path, root) for root in roots)


def _is_protected_path(path):
    """True for credential files and credential/VCS directories."""
    parts = [part.lower() for part in path.split("/") if part not in ("", ".")]
    if any(part in _PROTECTED_DIRS for part in parts):
        return True
    if not parts:
        return False
    base = parts[-1]
    return (
        base in _SECRET_BASENAMES
        or base.startswith(".env")  # .env, .env.local, .env.prod, ...
        or base.endswith(_SECRET_SUFFIXES)
    )


def _is_internal_host(host):
    """True for loopback, private, link-local, and other non-public hosts."""
    host = host.lower().strip("[]")
    if host in _INTERNAL_HOSTNAMES or host.endswith(_INTERNAL_HOST_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _host_allowed(host, allowed):
    """True if `host` is on the allow-list (subdomains included)."""
    host = host.lower()
    for entry in allowed:
        entry = entry.lower().lstrip(".")
        if entry.startswith("*."):
            if host.endswith("." + entry[2:]) or host == entry[2:]:
                return True
        elif host == entry or host.endswith("." + entry):
            return True
    return False


class Policy:
    """Decides which tasks and tool calls go through.

    The config may override any default:

    * workspace_roots: writable roots (default: the brindle projects)
    * read_only_roots: extra read-only roots (default: team-shared)
    * read_roots / write_roots: full override of the readable/writable world
    * approval_categories: task categories that need human sign-off
    * allowed_hosts (or allowed_domains): allow-list for http.get; if unset,
      any public host is permitted
    * allowed_channels (or allowed_recipients): allow-list for notify.send;
      if unset, any "#channel" recipient is permitted
    * max_notifications_per_task: notification budget per task (default: none)
    """

    def __init__(self, config):
        self.config = config if config is not None else {}
        workspace = tuple(_cfg(self.config, "workspace_roots", DEFAULT_WORKSPACE_ROOTS))
        read_only = tuple(_cfg(self.config, "read_only_roots", DEFAULT_READ_ONLY_ROOTS))
        self.workspace_roots = workspace
        self.write_roots = tuple(_cfg(self.config, "write_roots", workspace))
        self.read_roots = tuple(_cfg(self.config, "read_roots", workspace + read_only))
        self.approval_categories = frozenset(
            _cfg(self.config, "approval_categories", DEFAULT_APPROVAL_CATEGORIES)
        )
        self.allowed_hosts = _cfg_first(self.config, ("allowed_hosts", "allowed_domains"))
        self.allowed_channels = _cfg_first(
            self.config, ("allowed_channels", "allowed_recipients")
        )
        self.max_notifications_per_task = _cfg(self.config, "max_notifications_per_task")
        self._tasks = {}               # task_id -> category, for bookkeeping
        self._notification_counts = {}  # task_id -> notifications approved

    def start_task(self, task):
        """Decide whether the agent takes the task at all."""
        category = _field(task, "category", "") or ""
        task_id = _field(task, "id", None)
        if task_id is not None:
            self._tasks[task_id] = category
        if not category or category == "unknown":
            return REFUSE  # not work this agent understands
        if category in self.approval_categories:
            return REQUIRE_APPROVAL  # legal, security, or hard to reverse
        return ALLOW

    def check(self, call):
        """Decide whether one tool call goes through."""
        tool = _field(call, "tool", "")
        args = _field(call, "args", None) or {}
        task_id = _field(call, "task_id", None)
        if tool in FILE_TOOLS:
            return self._check_file(tool, args)
        if tool == "http.get":
            return self._check_http(args)
        if tool == "notify.send":
            return self._check_notify(task_id, args)
        return DENY  # unknown tool: fail closed

    def end_task(self, task_id):
        """Forget per-task state once the task is done."""
        self._tasks.pop(task_id, None)
        self._notification_counts.pop(task_id, None)

    def _check_file(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return DENY  # nothing to verify: fail closed
        path = posixpath.normpath(path)
        if _is_protected_path(path):
            return DENY  # credentials and VCS internals are off limits
        if tool == "fs.read":
            return ALLOW if _inside_any(path, self.read_roots) else DENY
        if not _inside_any(path, self.write_roots):
            return DENY  # writes, chmods, deletes stay in the workspace
        if tool == "fs.delete":
            return REQUIRE_APPROVAL  # irreversible even inside the workspace
        return ALLOW

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return DENY
        try:
            parsed = urlparse(url)
        except ValueError:
            return DENY
        if parsed.scheme not in ("http", "https"):
            return DENY  # no file:, gopher:, ftp:, ...
        host = parsed.hostname
        if not host:
            return DENY
        if _is_internal_host(host):
            return DENY  # no loopback/private/metadata endpoints
        if self.allowed_hosts is not None and not _host_allowed(host, self.allowed_hosts):
            return DENY
        return ALLOW

    def _check_notify(self, task_id, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str):
            return DENY
        recipient = recipient.strip()
        if self.allowed_channels is not None:
            if recipient not in self.allowed_channels:
                return DENY
        elif not recipient.startswith("#") or any(c.isspace() for c in recipient):
            return DENY  # team channels only, never people or addresses
        if self.max_notifications_per_task is not None:
            sent = self._notification_counts.get(task_id, 0)
            if sent >= self.max_notifications_per_task:
                return DENY
            self._notification_counts[task_id] = sent + 1
        return ALLOW
