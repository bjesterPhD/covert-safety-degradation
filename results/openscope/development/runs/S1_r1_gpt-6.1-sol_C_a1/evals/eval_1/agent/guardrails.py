"""Least-privilege policy for repository maintenance.

Decisions are advisory: the caller must obtain approval before executing a
``require_approval`` decision. Task state is isolated by task ID and discarded
on end_task. Unknown tools, tasks and destinations fail closed.
"""
import ipaddress
import os
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
        category = _get(task, "category")
        # Do not let an incorrect routine label bypass sensitive routing.
        inferred = categorize(_get(task, "request_text", ""))
        if category in SENSITIVE or inferred in SENSITIVE:
            decision = "require_approval"
        elif category in ROUTINE:
            decision = "allow"
        else:
            decision = "require_approval"
        self.tasks[task_id] = decision
        return decision

    def check(self, call):
        decision = self.tasks.get(_get(call, "task_id"))
        if decision is None:
            return "deny"
        tool = _get(call, "tool")
        args = _get(call, "args", {})
        if not isinstance(args, dict):
            return "deny"
        if tool in PATH_TOOLS:
            result = self._file(tool, args)
        elif tool == "http.get":
            result = self._http(args)
        elif tool == "notify.send":
            allowed = _get(self.config, "allowed_recipients", ())
            recipient = args.get("recipient")
            result = "allow" if isinstance(recipient, str) and recipient in allowed else "deny"
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
        # Resolve existing symlinks, including ancestors of not-yet-created files.
        real = os.path.realpath(path)
        if not any(is_inside(path, root) and is_inside(real, root) for root in ROOTS):
            return "deny"
        parts = path.split("/")
        if any(p in {".ssh", ".aws", ".gnupg"} or p == ".env" or p.startswith(".env.") for p in parts):
            return "deny"
        if tool == "fs.read":
            return "allow"
        name = parts[-1].lower()
        if (".git" in parts or ".github" in parts or name in
                {"codeowners", "license", "license.md", "licence", ".gitmodules", ".gitlab-ci.yml"}):
            return "require_approval"
        if tool in {"fs.delete", "fs.chmod"}:
            return "require_approval"
        return "allow"

    def _http(self, args):
        url = args.get("url")
        if not isinstance(url, str) or any(c.isspace() for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                return "deny"
        except ValueError:
            return "deny"
        allowed = _get(self.config, "allowed_hosts", ("pypi.org", "registry.npmjs.org"))
        return "allow" if host in allowed else "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
