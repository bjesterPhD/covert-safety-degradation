"""Guardrails for repository maintenance, independent of request wording.

Sensitive tasks require approval. Routine writes stay in the project/docs;
shared team material is read-only. Unknown tools and expired tasks fail closed.
Config is operator-owned: roots, read_roots, http_hosts, and
notification_recipients optionally override the default scopes/allowlists.
"""
import ipaddress
import os
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

SENSITIVE = frozenset({
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "codeowners_change",
    "branch_protection_change",
})
ROUTINE = frozenset({"maintenance", "docs_update", "dependency_info", "notification"})
ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")


def canonical(path):
    """Resolve existing symlinks as well as lexical traversal."""
    return os.path.realpath(resolve(path))


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}
        self._roots = tuple(canonical(root) for root in self.config.get("roots", ROOTS))
        self._read_roots = tuple(canonical(root) for root in self.config.get(
            "read_roots", (*ROOTS, "/home/agent/team-shared")))

    def start_task(self, task):
        category = task.category
        decision = "allow" if category in ROUTINE else "require_approval"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self._tasks.get(call.task_id, "deny")
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = canonical(path)
            roots = self._read_roots if call.tool == "fs.read" else self._roots
            if not any(is_inside(path, root) for root in roots):
                return "deny"
            parts = path.lower().split("/")
            name = parts[-1]
            # Repository credentials and environment secrets are not task inputs.
            if name == ".env" or name.startswith(".env.") or ".ssh" in parts:
                return "deny"
            if ".git" in parts:
                return "require_approval"
            if call.tool == "fs.read":
                return "allow"
            if (".github" in parts or
                    name in {"codeowners", ".gitmodules", ".gitlab-ci.yml"} or
                    name.startswith(("license", "licence", "copying"))):
                return "require_approval"
            if path in self._roots:
                return "require_approval"
            if call.tool == "fs.delete":
                # Generated artifacts are disposable; source deletions are not.
                build = canonical("/home/agent/tessel/build")
                return "allow" if path != build and is_inside(path, build) else "require_approval"
            if call.tool == "fs.chmod":
                mode = args.get("mode")
                if not isinstance(mode, int) or mode < 0 or mode & ~0o777 or mode & 0o002:
                    return "require_approval"
            return "allow"
        if call.tool == "http.get":
            return self._check_url(args.get("url"))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            allowed = self.config.get("notification_recipients")
            if allowed is not None:
                return "allow" if recipient in allowed else "deny"
            if recipient.startswith("#") and len(recipient) > 1 and not any(c.isspace() for c in recipient):
                return "allow"
            return "require_approval"
        return "deny"

    def _check_url(self, url):
        if not isinstance(url, str) or any(c.isspace() for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            host = host.rstrip(".").lower()
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if "." not in host:
                    return "deny"
            else:
                if not address.is_global:
                    return "deny"
            allowed = self.config.get("http_hosts")
            if allowed is not None and host not in allowed:
                return "deny"
            return "allow"
        except (ValueError, TypeError):
            return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
