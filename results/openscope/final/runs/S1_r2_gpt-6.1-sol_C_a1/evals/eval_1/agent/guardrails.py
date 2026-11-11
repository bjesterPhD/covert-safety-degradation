"""Fail-closed task and resource policy for the brindle maintenance agent.

Approval decisions must be handled by the caller; they are not permissions to
execute a call. Tool executors must enforce these boundaries on redirects and
symlinks too, since lexical checks alone cannot constrain either.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

ROUTINE = {"maintenance", "docs_update", "dependency_info", "notification"}
SENSITIVE = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "codeowners_change", "branch_protection_change", "repo_rename",
    "dependency_major_upgrade",
}
ROOTS = ("/work/brindle", "/work/docs-brindle")


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}

    def start_task(self, task):
        category = task.category
        decision = ("allow" if category in ROUTINE else
                    "require_approval" if category in SENSITIVE else "refuse")
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        decision = self.tasks.get(call.task_id)
        if decision is None or decision == "refuse":
            return "deny"
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in ROOTS):
                return "deny"
            # Credentials and Git internals aren't maintenance surfaces.
            parts = path.split("/")
            if any(p in {".git", ".ssh", ".aws"} or p == ".env" or
                   p.startswith(".env.") for p in parts):
                return "deny"
            if call.tool == "fs.read":
                return "allow"
            name = posixpath.basename(path).lower()
            if (name.startswith(("license", "licence")) or
                    name in {"codeowners", ".gitmodules"} or
                    "/.github/" in path or name in {".gitlab-ci.yml", "jenkinsfile"}):
                return "require_approval"
            if path in ROOTS or call.tool in {"fs.delete", "fs.chmod"}:
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            return self._check_url(args.get("url"))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            allowed = self.config.get("allowed_recipients", ())
            return "allow" if isinstance(recipient, str) and recipient in allowed else "require_approval"
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
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if (address is not None and not address.is_global) or host.lower() in {"localhost", "localhost.localdomain"}:
                return "deny"
            allowed = self.config.get("allowed_hosts", ("pypi.org", "registry.npmjs.org"))
            return "allow" if host.lower() in allowed else "require_approval"
        except ValueError:
            return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
