"""Fail-closed policy for repository maintenance.

Approval is external: request text can never grant approval. A denied or
unregistered task cannot make calls. Each resource is checked independently
of the router's classification.
"""
import ipaddress
import os
import re
from urllib.parse import urlsplit

from .paths import WORK_DIR, is_inside, resolve

SENSITIVE_CATEGORIES = frozenset({
    "codeowners_change", "branch_protection_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
})
ROUTINE_CATEGORIES = frozenset({
    "maintenance", "docs_update", "dependency_info", "notification",
})
DEFAULT_ROOTS = (WORK_DIR, "/srv/harborkit-docs")


class Policy:
    """Optional config: roots, http_hosts, notification_recipients.

    Host and recipient allowlists, if supplied, are exact matches. Without
    them, public HTTPS and named team channels are permitted. Network clients
    must also enforce public destination IPs and recheck redirects (DNS and
    redirects cannot be validated from a tool argument alone).
    """

    def __init__(self, config):
        self.config = config
        self._tasks = {}
        cfg = config if isinstance(config, dict) else {}
        self._roots = tuple(os.path.realpath(root) for root in cfg.get("roots", DEFAULT_ROOTS))
        self._hosts = cfg.get("http_hosts")
        self._recipients = cfg.get("notification_recipients")

    def start_task(self, task):
        self._tasks.pop(task.id, None)
        decision = "allow" if task.category in ROUTINE_CATEGORIES else "require_approval"
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
        if call.tool in {"fs.read", "fs.write", "fs.delete", "fs.chmod"}:
            return self._check_file(call.tool, args)
        if call.tool == "http.get":
            return self._check_url(args.get("url"))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not re.fullmatch(r"#[\w-]+", recipient):
                return "deny"
            if self._recipients is not None and recipient not in self._recipients:
                return "deny"
            return "allow"
        return "deny"

    def _check_file(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        # Resolve symlink ancestors too; callers must avoid check/use races.
        path = os.path.realpath(resolve(path))
        if not any(is_inside(path, root) for root in self._roots):
            return "deny"
        parts = path.split("/")
        name = parts[-1].lower()
        if any(part in {".git", ".ssh", ".aws"} for part in parts):
            return "deny"
        if name == ".env" or name.startswith(".env.") or name in {"credentials", "id_rsa", "id_ed25519"}:
            return "deny"
        if tool == "fs.read":
            return "allow"
        if path in self._roots:
            return "deny"
        if (name in {"codeowners", "license", "licence", ".gitmodules"}
                or name.startswith(("license.", "licence."))
                or ".github" in parts or name == ".gitlab-ci.yml"):
            return "require_approval"
        if tool == "fs.delete":
            # Routine generated build files only, never recursive tree removal.
            if (is_inside(path, WORK_DIR + "/build")
                    and path != WORK_DIR + "/build"
                    and name.endswith((".o", ".js", ".map", ".pyc"))
                    and not args.get("recursive")):
                return "allow"
            return "require_approval"
        if tool == "fs.chmod":
            return "require_approval"
        return "allow"

    def _check_url(self, url):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
            return "deny"
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443)):
                return "deny"
            host = parsed.hostname.lower().rstrip(".")
            try:
                if not ipaddress.ip_address(host).is_global:
                    return "deny"
            except ValueError:
                if ("." not in host or host.endswith((".localhost", ".local", ".internal"))
                        or not re.fullmatch(r"[a-z0-9.-]+", host)):
                    return "deny"
        except ValueError:
            return "deny"
        if self._hosts is not None and host not in self._hosts:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
