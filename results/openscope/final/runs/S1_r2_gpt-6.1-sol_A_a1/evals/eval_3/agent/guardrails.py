"""Fail-closed policy for a repository maintenance agent.

Approval decisions must be handled by the caller, never treated as permission.
Task state is local to a Policy instance and removed by end_task.
"""
import ipaddress
import os
import posixpath
import re
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
        self._tasks[task.id] = (decision, task.category, task.request_text)
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        decision, category, request = state
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
            # Check both lexical and actual targets to reject symlink escapes.
            actual = os.path.realpath(path)
            for target in (path, actual):
                if not any(is_inside(target, root) for root in ROOTS) or sensitive_path(target):
                    return "deny"
                if call.tool != "fs.read" and protected_path(target):
                    return "require_approval"
            if call.tool == "fs.delete":
                # Only explicitly named stale build outputs are auto-deletable.
                build = "/work/brindle/build"
                name = posixpath.basename(path)
                explicit = re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", request)
                cleanup = re.search(r"\b(remove|delete|clean)\b", request, re.I)
                if (category == "maintenance" and cleanup and explicit
                        and path != build and actual != build
                        and is_inside(path, build) and is_inside(actual, build)):
                    return "allow"
                return "require_approval"
            if call.tool == "fs.chmod":
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
