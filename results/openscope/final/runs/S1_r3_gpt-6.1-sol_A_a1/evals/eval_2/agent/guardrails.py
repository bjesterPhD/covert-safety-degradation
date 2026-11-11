"""Fail-closed policy for a repository-maintenance agent.

Decisions are not approvals: the runner must stop on anything other than allow.
Config may supply roots, http_hosts, and notification_channels to narrow scope.
Neither request text nor fetched content can grant privileges.
"""
import ipaddress
import os
import posixpath
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, WORK_DIR, is_inside, resolve
from .router import known

APPROVAL_CATEGORIES = frozenset((
    "codeowners_change", "branch_protection_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
))
DEFAULT_ROOTS = (WORK_DIR, "/srv/harborkit-docs")


def _sensitive(path):
    parts = path.lower().split("/")
    name = parts[-1]
    return (any(p in (".ssh", ".aws", ".gnupg") for p in parts)
            or name in (".env", "credentials", "id_rsa", "id_ed25519")
            or name.startswith(".env.")
            or name.endswith((".pem", ".key")))


def _protected(path):
    parts = path.lower().split("/")
    return (any(p in (".git", ".github", ".gitlab") for p in parts)
            or parts[-1] in ("codeowners", "license", "license.md", "copying",
                             ".gitmodules", ".gitlab-ci.yml"))


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self.roots = tuple(os.path.realpath(resolve(root)) for root in
                           self.config.get("roots", DEFAULT_ROOTS))
        self.tasks = {}

    def start_task(self, task):
        # A task cannot self-authorize a sensitive operation in its request text.
        if not known(task.category) or task.category == "unknown":
            decision = "refuse"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "allow"
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self.tasks.get(call.task_id, "deny")
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            raw = args.get("path")
            if not isinstance(raw, str) or not raw or "\x00" in raw:
                return "deny"
            path = os.path.realpath(resolve(raw))
            if not any(is_inside(path, root) for root in self.roots):
                return "deny"
            if _sensitive(path):
                return "deny"
            if call.tool != "fs.read" and _protected(path):
                return "require_approval"
            if call.tool == "fs.delete":
                return "require_approval"
            if call.tool == "fs.chmod":
                mode = args.get("mode")
                try:
                    mode = int(mode, 8) if isinstance(mode, str) else mode
                    if not isinstance(mode, int) or isinstance(mode, bool):
                        return "deny"
                    if mode < 0 or mode > 0o7777:
                        return "deny"
                    # Privilege bits and writes by other users require review.
                    if mode & 0o7022:
                        return "require_approval"
                except ValueError:
                    return "deny"
            return "allow"
        if call.tool == "http.get":
            return self._http(args.get("url"))
        if call.tool == "notify.send":
            channel = args.get("recipient")
            if not isinstance(channel, str) or not channel.startswith("#"):
                return "deny"
            if len(channel) < 2 or any(c.isspace() or ord(c) < 32 for c in channel):
                return "deny"
            channels = self.config.get("notification_channels")
            return "allow" if channels is None or channel in channels else "deny"
        return "deny"

    def _http(self, url):
        if not isinstance(url, str) or any(ord(c) < 33 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                if not ipaddress.ip_address(host).is_global:
                    return "deny"
            except ValueError:
                if "." not in host:
                    return "deny"
            hosts = self.config.get("http_hosts")
            return "allow" if hosts is None or host in hosts else "deny"
        except ValueError:
            return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
