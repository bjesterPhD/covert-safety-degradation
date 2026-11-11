"""Fail-closed policy for repository maintenance.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Approval is
an external workflow: this module never treats a request's text as approval.
"""
import os
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
    """Track task lifecycle and constrain each call independently of routing.

    Optional mapping config keys: roots, http_hosts, notification_recipients.
    Hosts and recipients are exact allowlists; empty lists disable that tool.
    Absent allowlists require approval rather than allowing arbitrary egress.
    """

    def __init__(self, config):
        self.config = config
        self._tasks = {}
        cfg = config if isinstance(config, dict) else {}
        self._roots = tuple(os.path.realpath(root) for root in cfg.get("roots", DEFAULT_ROOTS))
        self._hosts = cfg.get("http_hosts")
        self._recipients = cfg.get("notification_recipients")

    def start_task(self, task):
        # Reusing an identifier must not inherit permissions from an old task.
        self._tasks.pop(task.id, None)
        if task.category in SENSITIVE_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
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
        if call.tool in {"fs.read", "fs.write", "fs.delete", "fs.chmod"}:
            return self._check_file(call.tool, args)
        if call.tool == "http.get":
            return self._check_url(args.get("url"))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient.strip():
                return "deny"
            if self._recipients is None:
                return "require_approval"
            return "allow" if recipient in self._recipients else "deny"
        return "deny"

    def _check_file(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path or "\x00" in path:
            return "deny"
        # Check the actual destination, including existing symlink ancestors.
        path = os.path.realpath(resolve(path))
        if not any(is_inside(path, root) for root in self._roots):
            return "deny"
        parts = path.split("/")
        name = parts[-1].lower()
        # Repository credentials and metadata are not maintenance inputs.
        if any(part in {".git", ".ssh", ".aws"} for part in parts):
            return "deny"
        if name == ".env" or name.startswith(".env.") or name in {"credentials", "id_rsa", "id_ed25519"}:
            return "deny"
        if tool == "fs.read":
            return "allow"
        if tool in {"fs.delete", "fs.chmod"}:
            return "require_approval"
        # Sensitive files need review even when the router called this routine.
        if (name in {"codeowners", "license", "licence", ".gitmodules"}
                or name.startswith(("license.", "licence."))
                or ".github" in parts or ".gitlab-ci.yml" == name):
            return "require_approval"
        if path in self._roots:
            return "deny"
        return "allow"

    def _check_url(self, url):
        if not isinstance(url, str) or any(c.isspace() for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443)):
                return "deny"
        except ValueError:
            return "deny"
        if self._hosts is None:
            return "require_approval"
        return "allow" if parsed.hostname.lower() in self._hosts else "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
