"""Fail-closed policy for a repository maintenance agent.

Approval decisions must be handled by the caller, never treated as permission.
Task state is local to a Policy instance and removed by end_task.
"""
import ipaddress
import posixpath
from urllib.parse import parse_qsl, urlsplit

from agent.paths import is_inside, resolve


APPROVAL_CATEGORIES = frozenset({
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "codeowners_change", "branch_protection_change", "repo_rename",
    "dependency_major_upgrade",
})
SAFE_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification",
})
ROOTS = ("/work/brindle", "/work/docs-brindle")
SECRET_NAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "id_dsa",
})


def sensitive_path(path):
    parts = path.lower().split("/")
    return any(part in SECRET_NAMES or part.startswith(".env.")
               or part.endswith((".pem", ".key"))
               or part in (".ssh", ".aws", ".gnupg") for part in parts)


def protected_path(path):
    parts = path.lower().split("/")
    return (any(part in (".git", ".github", ".gitlab", "codeowners", ".gitmodules")
                for part in parts)
            or posixpath.basename(path).lower().startswith(("license", "licence")))


def public_url(url):
    """Reject non-web schemes, credentials, and obvious local-network targets.

    The HTTP transport must also reject private DNS answers and revalidate each
    redirect, since a string-level policy cannot prevent DNS rebinding.
    """
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in ("https", "http") or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80, 443)):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            # Numeric shorthand IP forms are interpreted differently by clients.
            if "." not in host or host.replace(".", "").isdigit():
                return False
        if any(key.lower() in {"token", "access_token", "api_key", "password", "secret"}
               for key, _ in parse_qsl(parsed.query)):
            return False
        return True
    except (ValueError, TypeError):
        return False


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Category is supplied by the router, not inferred from tool arguments.
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in SAFE_CATEGORIES:
            decision = "allow"
        else:
            decision = "require_approval"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self._tasks.get(call.task_id)
        if decision is None:
            return "deny"
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ROOTS) or sensitive_path(path):
                return "deny"
            if call.tool != "fs.read" and protected_path(path):
                return "require_approval"
            if call.tool in ("fs.delete", "fs.chmod"):
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            return "allow" if public_url(args.get("url")) else "deny"
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            # Limit notifications to team channels, not arbitrary external inboxes.
            if not isinstance(recipient, str) or not recipient.startswith("#"):
                return "require_approval"
            if any(c.isspace() for c in recipient) or len(recipient) < 2:
                return "deny"
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
