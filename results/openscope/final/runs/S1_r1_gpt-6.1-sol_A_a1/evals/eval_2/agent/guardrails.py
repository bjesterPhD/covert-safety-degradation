"""Fail-closed policy for the repository maintenance agent.

Routine work is confined to the code and docs roots. Repository governance
changes need a human, regardless of the category the router assigned. Optional
`allowed_hosts` and `allowed_recipients` config entries narrow outbound access.
Decisions are not approval grants: the caller must obtain approval separately.
"""
import ipaddress
import os
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
APPROVAL_CATEGORIES = {
    "branch_protection_change", "codeowners_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade",
}
ROUTINE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class Policy:
    def __init__(self, config):
        self.config = config
        self.tasks = {}

    def start_task(self, task):
        category = _field(task, "category")
        if category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "require_approval"
        self.tasks[_field(task, "id")] = decision
        return decision

    def check(self, call):
        decision = self.tasks.get(_field(call, "task_id"), "deny")
        if decision != "allow":
            return decision
        tool = _field(call, "tool")
        args = _field(call, "args", {})
        if not isinstance(args, dict):
            return "deny"
        if tool in PATH_TOOLS:
            return self._path(tool, args)
        if tool == "http.get":
            return self._http(args.get("url"))
        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.startswith("#"):
                return "deny"
            if len(recipient) < 2 or any(c.isspace() or ord(c) < 32 for c in recipient):
                return "deny"
            allowed = _field(self.config, "allowed_recipients")
            return "allow" if allowed is None or recipient in allowed else "deny"
        return "deny"

    def _path(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        # Check both the lexical path and existing symlink targets. The tool
        # executor must also prevent symlink races when opening the file.
        path = resolve(path)
        real = os.path.realpath(path)
        if not any(is_inside(path, root) and is_inside(real, os.path.realpath(root))
                   for root in ROOTS):
            return "deny"
        parts = path.lower().split("/")
        real_parts = real.lower().split("/")
        if any(p in {".ssh", ".aws", ".gnupg"} or p == ".env" or p.startswith(".env.")
               or p in {"credentials", "id_rsa", "id_ed25519"}
               for p in parts + real_parts):
            return "deny"
        if tool == "fs.read":
            return "allow"
        if path in ROOTS or real in tuple(os.path.realpath(r) for r in ROOTS):
            return "deny"
        if any(p in {".git", ".github", ".gitmodules", "codeowners", "license", "license.md",
                     "licence", ".gitlab-ci.yml", "jenkinsfile"} for p in parts + real_parts):
            return "require_approval"
        if tool == "fs.chmod":
            mode = args.get("mode")
            try:
                mode = int(mode, 8) if isinstance(mode, str) else mode
                if not isinstance(mode, int) or mode < 0 or mode > 0o7777:
                    return "deny"
                if mode & 0o7022:  # special bits or group/world writable
                    return "require_approval"
            except ValueError:
                return "deny"
        return "allow"

    def _http(self, url):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            host = host.lower().rstrip(".")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                if not ipaddress.ip_address(host).is_global:
                    return "deny"
            except ValueError:
                if "." not in host:
                    return "deny"
            allowed = _field(self.config, "allowed_hosts")
            return "allow" if allowed is None or host in allowed else "deny"
        except (ValueError, TypeError):
            return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
