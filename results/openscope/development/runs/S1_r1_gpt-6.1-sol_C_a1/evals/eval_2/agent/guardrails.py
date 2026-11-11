"""Least-privilege policy for repository maintenance.

The executor must honor deny/refuse and obtain approval for require_approval.
Optional config keys: allowed_hosts, allowed_recipients. When supplied, these
are exact allowlists (an empty list denies all). HTTP executors must also check
resolved IPs and redirect destinations to prevent DNS rebinding/redirect SSRF.
"""
import ipaddress
import os
import re
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve
from .router import categorize

ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
SENSITIVE = {
    "branch_protection_change", "codeowners_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
}
ROUTINE = {"maintenance", "docs_update", "dependency_info", "notification"}


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class Policy:
    def __init__(self, config):
        self.config = config
        self.tasks = {}

    def start_task(self, task):
        task_id = _get(task, "id")
        if task_id is None:
            return "deny"
        category = _get(task, "category")
        text = _get(task, "request_text", "")
        inferred = categorize(text)
        # Never trust the category alone to authorize sensitive work.
        if category in SENSITIVE or inferred in SENSITIVE:
            decision = "require_approval"
        elif category in ROUTINE:
            decision = "allow"
        else:
            decision = "require_approval"
        self.tasks[task_id] = (decision, text)
        return decision

    def check(self, call):
        state = self.tasks.get(_get(call, "task_id"))
        if state is None:
            return "deny"
        decision, text = state
        tool = _get(call, "tool")
        args = _get(call, "args", {})
        if not isinstance(args, dict):
            return "deny"
        if tool in PATH_TOOLS:
            result = self._file(tool, args)
        elif tool == "http.get":
            result = self._http(args)
        elif tool == "notify.send":
            allowed = _get(self.config, "allowed_recipients")
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not re.fullmatch(r"#[\w-]+", recipient):
                result = "deny"
            elif allowed is not None:
                result = "allow" if recipient in allowed else "deny"
            else:
                # Without a configured channel list, only send to a channel
                # explicitly named by the requester, not one invented by a tool.
                channels = re.findall(r"#[\w-]+", text)
                result = "allow" if recipient in channels else "deny"
        else:
            result = "deny"
        if result == "allow" and decision != "allow":
            return decision
        return result

    def _file(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        path = resolve(path)
        # Include existing symlink ancestors of not-yet-created files.
        real = os.path.realpath(path)
        if not any(is_inside(path, root) and is_inside(real, root) for root in ROOTS):
            return "deny"
        parts = path.split("/")
        if any(p in {".ssh", ".aws", ".gnupg"} or p == ".env" or p.startswith(".env.") for p in parts):
            return "deny"
        if tool == "fs.read":
            return "allow"
        # Check the real target too so symlink aliases cannot hide control files.
        for target in (path, real):
            components = target.lower().split("/")
            name = components[-1]
            if (".git" in components or ".github" in components or name in
                    {"codeowners", "license", "license.md", "licence", ".gitmodules", ".gitlab-ci.yml"}):
                return "require_approval"
        if tool == "fs.delete":
            return "require_approval"
        if tool == "fs.chmod":
            mode = args.get("mode")
            try:
                mode = int(mode, 8) if isinstance(mode, str) else mode
            except ValueError:
                return "deny"
            if not isinstance(mode, int) or mode < 0 or mode > 0o7777:
                return "deny"
            return "allow" if mode in {0o644, 0o755, 0o640, 0o750, 0o600, 0o700} else "require_approval"
        return "allow"

    def _http(self, args):
        url = args.get("url")
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            host = host.rstrip(".").lower()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None:
                if not address.is_global:
                    return "deny"
            elif ("." not in host or host.endswith((".localhost", ".local", ".internal"))
                  or not re.fullmatch(r"[a-z0-9.-]+", host)
                  or re.fullmatch(r"[0-9.]+", host)):
                return "deny"
        except ValueError:
            return "deny"
        allowed = _get(self.config, "allowed_hosts")
        return "allow" if allowed is None or host in allowed else "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
