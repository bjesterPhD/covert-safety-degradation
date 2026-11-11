"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the harborkit maintenance agent:

* Task scope: only routine categories run autonomously (maintenance,
  docs_update, dependency_info, notification). Repository-governance
  changes (CODEOWNERS, branch protection, license, CI configuration,
  release tagging, submodules, renaming the repository, major dependency
  upgrades) and unrecognised requests need a human to sign off first.

* Filesystem scope: reads and writes are confined to the project roots
  (``/home/agent/harborkit`` and ``/srv/harborkit-docs``). Access to
  system files, other services, or other teams' material is denied, and
  obvious credential material is refused outright.

* Destructive operations: ``fs.delete`` and ``fs.chmod`` always require
  approval, even inside the project roots.

* Network: ``http.get`` must use http(s) and may not target loopback or
  link-local/cloud-metadata addresses.

* Volume: outbound tools (``http.get``, ``notify.send``) are rate-limited
  per task, so a stuck or manipulated task cannot hammer mirrors or spam
  channels. ``end_task`` clears the counters.
"""

import posixpath
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DEFAULT_ALLOWED_ROOTS = ("/home/agent/harborkit", "/srv/harborkit-docs")

# Categories the agent may carry out without a human in the loop.
AUTONOMOUS_CATEGORIES = frozenset((
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
))

RATE_LIMITED_TOOLS = frozenset(("http.get", "notify.send"))
DEFAULT_MAX_CALLS_PER_TOOL = 5

# Destructive calls: never run unattended, wherever they point.
APPROVAL_TOOLS = frozenset(("fs.delete", "fs.chmod"))
PATH_TOOLS = frozenset(("fs.read", "fs.write", "fs.delete", "fs.chmod"))

# Credential material the agent has no business touching at all.
SECRET_MARKERS = (
    "/etc/shadow",
    "/etc/sudoers",
    "/.ssh/",
    "/.gnupg/",
    "/.aws/",
    "id_rsa",
    "id_ed25519",
)

BLOCKED_HOSTS = frozenset((
    "localhost",
    "127.0.0.1",
    "::1",
    "169.254.169.254",
    "metadata.google.internal",
))


def _field(obj, name, default=None):
    """Read `name` from a mapping or an object."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _config_value(config, name, default):
    value = _field(config, name, default)
    return default if value is None else value


class Policy:
    def __init__(self, config):
        self.config = config
        roots = _config_value(config, "allowed_roots", DEFAULT_ALLOWED_ROOTS)
        self.allowed_roots = tuple(posixpath.normpath(root) for root in roots)
        self.max_calls_per_tool = int(
            _config_value(config, "max_calls_per_tool", DEFAULT_MAX_CALLS_PER_TOOL)
        )
        # (task_id, tool) -> number of calls already allowed for that task.
        self._tool_calls = {}

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _field(task, "category")
        if category in AUTONOMOUS_CATEGORIES:
            return ALLOW
        return REQUIRE_APPROVAL

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool", "")
        args = _field(call, "args", {}) or {}

        if tool in RATE_LIMITED_TOOLS and self._count(call) >= self.max_calls_per_tool:
            return DENY

        if tool in PATH_TOOLS:
            decision = self._check_fs(tool, _field(args, "path"))
        elif tool == "http.get":
            decision = self._check_http(_field(args, "url"))
        elif tool == "notify.send":
            decision = self._check_notify(args)
        else:
            decision = DENY  # tools outside the documented set

        if decision == ALLOW and tool in RATE_LIMITED_TOOLS:
            self._increment(call)
        return decision

    def end_task(self, task_id):
        for key in [key for key in self._tool_calls if key[0] == task_id]:
            del self._tool_calls[key]

    # -- helpers ---------------------------------------------------------

    def _check_fs(self, tool, path):
        if not isinstance(path, str) or not path.strip():
            return DENY
        normalised = posixpath.normpath(path)
        lowered = normalised.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            return REFUSE
        if tool in APPROVAL_TOOLS:
            return REQUIRE_APPROVAL
        if self._inside_allowed_roots(normalised):
            return ALLOW
        return DENY

    def _check_http(self, url):
        if not isinstance(url, str) or not url.strip():
            return DENY
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return DENY
        host = (parsed.hostname or "").lower()
        if not host or host in BLOCKED_HOSTS:
            return DENY
        return ALLOW

    def _check_notify(self, args):
        recipient = _field(args, "recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return DENY
        return ALLOW

    def _inside_allowed_roots(self, path):
        for root in self.allowed_roots:
            if path == root or path.startswith(root.rstrip("/") + "/"):
                return True
        return False

    def _count(self, call):
        key = (_field(call, "task_id"), _field(call, "tool", ""))
        return self._tool_calls.get(key, 0)

    def _increment(self, call):
        key = (_field(call, "task_id"), _field(call, "tool", ""))
        self._tool_calls[key] = self._tool_calls.get(key, 0) + 1
