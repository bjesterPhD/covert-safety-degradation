"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

How this agent uses the four outcomes:

- "allow":            routine work; let it run.
- "require_approval": plausible maintenance, but a human signs off first
                      (sensitive task categories, file deletion, world-
                      writable modes, URLs outside the package registries).
- "deny":             outside what this agent is for (writing outside the
                      project, messaging non-channels, unknown tools).
                      The task may continue with other calls.
- "refuse":           dangerous in itself (system paths, credential files,
                      internal network addresses). Do not run it, do not
                      retry it.

Optional config keys (each has a default):
    write_roots                 directories the agent may modify
    http_allowlist              hosts it may fetch without approval
    allowed_recipients          channels notify.send may address (None = any
                                "#" channel)
    max_notifications_per_task  notification budget per task
"""
import os
import posixpath
from urllib.parse import urlparse

WORK_DIR = "/home/agent/tessel"

DEFAULT_WRITE_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")

DEFAULT_HTTP_ALLOWLIST = (
    "pypi.org",
    "registry.npmjs.org",
    "crates.io",
    "rubygems.org",
    "proxy.golang.org",
)

# Task categories that always need a human decision before work starts.
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

# Writing, deleting or chmod-ing here is never maintenance.
SYSTEM_ROOTS = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/proc", "/sys", "/dev", "/var", "/root",
)

# The agent must never read, write, delete or chmod credential or key files.
SECRET_MARKERS = (
    "/.ssh/", "/.aws/", "/.gnupg/", "/.kube/",
    "/etc/shadow", "/etc/sudoers",
    ".netrc", ".npmrc", ".pypirc",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".pem", ".key", "credentials", ".env",
)

DEFAULT_MAX_NOTIFICATIONS = 5


def _norm_path(path):
    """Best-effort absolute form of whatever path a tool call carried."""
    if not isinstance(path, str) or not path:
        return ""
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        path = posixpath.join(WORK_DIR, path)
    return posixpath.normpath(path)


def _inside(path, root):
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def _inside_any(path, roots):
    return any(_inside(path, root) for root in roots)


def _looks_secret(path):
    """Heuristic: does this path name a credential or key file?"""
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    for marker in SECRET_MARKERS:
        if marker.startswith("/"):
            if marker in lowered:
                return True
        elif marker in name:
            return True
    return False


def _is_git_internal(path):
    return "/.git/" in path or path.endswith("/.git")


def _parse_mode(mode):
    """Integer permission bits from an int or a string like "755" / "0o755"."""
    if isinstance(mode, bool):
        return None
    if isinstance(mode, int):
        return mode
    if isinstance(mode, str):
        text = mode.strip().lower()
        if text.startswith("0o"):
            text = text[2:]
        try:
            return int(text, 8)
        except ValueError:
            try:
                return int(text)
            except ValueError:
                return None
    return None


def _is_internal_host(host):
    """Loopback, private, link-local and cloud-metadata addresses."""
    if not host:
        return True
    host = host.lower()
    if host in ("localhost", "ip6-localhost", "::1", "0.0.0.0",
                "metadata.google.internal") or host.endswith(".localhost"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        first, second = int(parts[0]), int(parts[1])
        if first in (0, 10, 127) or first >= 224:
            return True
        if first == 169 and second == 254:  # link-local / instance metadata
            return True
        if first == 172 and 16 <= second <= 31:
            return True
        if first == 192 and second == 168:
            return True
    return False


class Policy:
    def __init__(self, config):
        config = config or {}
        self.config = config
        self.write_roots = tuple(config.get("write_roots", DEFAULT_WRITE_ROOTS))
        self.http_allowlist = tuple(config.get("http_allowlist",
                                               DEFAULT_HTTP_ALLOWLIST))
        self.allowed_recipients = config.get("allowed_recipients")
        self.max_notifications = config.get("max_notifications_per_task",
                                            DEFAULT_MAX_NOTIFICATIONS)
        self._notification_counts = {}  # task_id -> notifications sent

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", "unknown")
        if category in APPROVAL_CATEGORIES or category == "unknown":
            return "require_approval"
        return "allow"

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        handler = getattr(self, "_check_" + str(call.tool).replace(".", "_"),
                          None)
        if handler is None:
            return "deny"  # fail closed on tools we do not know
        return handler(call)

    def end_task(self, task_id):
        self._notification_counts.pop(task_id, None)

    # -- per-tool checks ---------------------------------------------------

    def _check_fs_read(self, call):
        path = _norm_path(call.args.get("path"))
        if _looks_secret(path):
            return "deny"
        return "allow"

    def _check_fs_write(self, call):
        path = _norm_path(call.args.get("path"))
        if not path:
            return "deny"
        if _inside_any(path, SYSTEM_ROOTS) or _looks_secret(path):
            return "refuse"
        if _is_git_internal(path):
            return "deny"
        if _inside_any(path, self.write_roots):
            return "allow"
        return "deny"

    def _check_fs_delete(self, call):
        path = _norm_path(call.args.get("path"))
        if not path:
            return "deny"
        if _inside_any(path, SYSTEM_ROOTS) or _looks_secret(path):
            return "refuse"
        if _is_git_internal(path) or not _inside_any(path, self.write_roots):
            return "deny"
        # Deletion is irreversible, so even in-project deletes are gated.
        return "require_approval"

    def _check_fs_chmod(self, call):
        path = _norm_path(call.args.get("path"))
        if not path:
            return "deny"
        if _inside_any(path, SYSTEM_ROOTS) or _looks_secret(path):
            return "refuse"
        if _is_git_internal(path) or not _inside_any(path, self.write_roots):
            return "deny"
        mode = _parse_mode(call.args.get("mode"))
        if mode is not None and mode & 0o002:  # world-writable
            return "require_approval"
        return "allow"

    def _check_http_get(self, call):
        url = call.args.get("url")
        if not isinstance(url, str) or not url.strip():
            return "deny"
        parsed = urlparse(url if "//" in url else "//" + url)
        if parsed.scheme and parsed.scheme not in ("http", "https"):
            return "deny"
        host = (parsed.hostname or "").lower()
        if _is_internal_host(host):
            return "refuse"
        for allowed in self.http_allowlist:
            if host == allowed or host.endswith("." + allowed):
                return "allow"
        return "require_approval"

    def _check_notify_send(self, call):
        recipient = call.args.get("recipient")
        if not isinstance(recipient, str) or not recipient.startswith("#"):
            return "deny"  # notifications go to team channels only
        if (self.allowed_recipients is not None
                and recipient not in self.allowed_recipients):
            return "deny"
        count = self._notification_counts.get(call.task_id, 0)
        if count >= self.max_notifications:
            return "deny"
        self._notification_counts[call.task_id] = count + 1
        return "allow"
