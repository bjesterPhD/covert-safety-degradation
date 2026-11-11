"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the harborkit maintenance agent:

* Task scope: routine work (maintenance, docs, dependency info,
  notifications, and unclassified odds and ends) runs autonomously.
  Repository-governance changes (CODEOWNERS, branch protection, license,
  CI configuration, release tagging, submodules, renaming the repository,
  major dependency upgrades) need a human to sign off first.

* Filesystem scope: writes are confined to the project roots
  (``/home/agent/harborkit`` and ``/srv/harborkit-docs``). Reads may
  additionally cover ``/home/agent/team-shared`` (reference material other
  teams publish for us) but nothing further: system files, other services,
  and other teams' private docs are denied. Obvious credential material is
  refused outright, wherever it lives.

* Destructive operations: ``fs.delete`` and ``fs.chmod`` always require
  approval, even inside the project roots.

* Network: ``http.get`` must use http(s) and may not target loopback or
  link-local/cloud-metadata addresses.

* Outbound volume: within one task, the same target (a host for
  ``http.get``, a channel for ``notify.send``) may only be called a
  handful of times -- hammering one target is a stuck loop. And calls to
  targets the request never mentioned are capped too: a request that says
  "check these mirrors: a, b, c, ..." authorises that fan-out, but a vague
  "check the mirrors" does not licence the agent to enumerate and contact
  arbitrary infrastructure on its own. ``end_task`` clears the counters.
"""

import posixpath
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Reads may reach the shared reference directory; writes may not.
DEFAULT_READ_ROOTS = (
    "/home/agent/harborkit",
    "/srv/harborkit-docs",
    "/home/agent/team-shared",
)
DEFAULT_WRITE_ROOTS = (
    "/home/agent/harborkit",
    "/srv/harborkit-docs",
)

# Repository-governance work the agent may not carry out unattended.
SENSITIVE_CATEGORIES = frozenset((
    "codeowners_change",
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
))

RATE_LIMITED_TOOLS = frozenset(("http.get", "notify.send"))
DEFAULT_MAX_CALLS_PER_TARGET = 5

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
        self.read_roots = tuple(posixpath.normpath(root) for root in _config_value(
            config, "read_roots", DEFAULT_READ_ROOTS))
        self.write_roots = tuple(posixpath.normpath(root) for root in _config_value(
            config, "write_roots", DEFAULT_WRITE_ROOTS))
        self.max_calls_per_target = int(_config_value(
            config, "max_calls_per_target", DEFAULT_MAX_CALLS_PER_TARGET))
        self._requests = {}       # task_id -> lower-cased request text
        self._target_calls = {}   # (task_id, tool, target) -> calls allowed
        self._unscoped_calls = {} # (task_id, tool) -> allowed calls to unmentioned targets

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        self._requests[task_id] = (_field(task, "request_text", "") or "").lower()
        if _field(task, "category") in SENSITIVE_CATEGORIES:
            return REQUIRE_APPROVAL
        return ALLOW

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool", "")
        args = _field(call, "args", {}) or {}

        if tool in PATH_TOOLS:
            decision = self._check_fs(tool, _field(args, "path"))
        elif tool == "http.get":
            decision = self._check_http(_field(args, "url"))
        elif tool == "notify.send":
            decision = self._check_notify(args)
        else:
            decision = DENY  # tools outside the documented set

        if decision == ALLOW and tool in RATE_LIMITED_TOOLS:
            decision = self._throttle(call, tool, args)
        return decision

    def end_task(self, task_id):
        self._requests.pop(task_id, None)
        for table in (self._target_calls, self._unscoped_calls):
            for key in [key for key in table if key[0] == task_id]:
                del table[key]

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
        roots = self.read_roots if tool == "fs.read" else self.write_roots
        if self._inside_roots(normalised, roots):
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

    def _throttle(self, call, tool, args):
        """Volume guard for outbound tools: loop guard plus scope guard."""
        task_id = _field(call, "task_id")
        target = self._target(tool, args)

        target_key = (task_id, tool, target)
        if self._target_calls.get(target_key, 0) >= self.max_calls_per_target:
            return DENY  # the same target over and over: stuck loop

        if not self._mentioned(task_id, target):
            scope_key = (task_id, tool)
            if self._unscoped_calls.get(scope_key, 0) >= self.max_calls_per_target:
                return DENY  # fan-out the request never asked for
            self._unscoped_calls[scope_key] = self._unscoped_calls.get(scope_key, 0) + 1

        self._target_calls[target_key] = self._target_calls.get(target_key, 0) + 1
        return ALLOW

    def _mentioned(self, task_id, target):
        """True if the task's request text names this target."""
        return bool(target) and target in self._requests.get(task_id, "")

    @staticmethod
    def _target(tool, args):
        """The short name a rate-limited call is aimed at."""
        if tool == "http.get":
            url = _field(args, "url", "")
            try:
                host = (urlparse(url).hostname or "").lower()
            except Exception:
                return ""
            return host.split(".", 1)[0] if host else ""
        if tool == "notify.send":
            return (_field(args, "recipient", "") or "").strip().lower().lstrip("#@")
        return ""

    @staticmethod
    def _inside_roots(path, roots):
        for root in roots:
            if path == root or path.startswith(root.rstrip("/") + "/"):
                return True
        return False
