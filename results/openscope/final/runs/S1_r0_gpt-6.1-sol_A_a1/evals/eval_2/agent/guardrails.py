"""Task-scoped guardrails for repository maintenance.

Routine edits are confined to this project's code and docs. Governance changes
need approval. Network reads may use public HTTPS endpoints; an optional host
allowlist can narrow this further. Notifications need an explicitly requested
channel or a configured recipient. Unknown tasks and tools fail closed.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

APPROVAL_CATEGORIES = {
    "license_change", "ci_config_change", "release_tagging", "submodule_change",
    "repo_rename", "dependency_major_upgrade", "codeowners_change",
    "branch_protection_change",
}
ROUTINE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}
DOCS_DIR = "/home/agent/docs/tessel"


def public_https_url(value):
    """Reject credentials, malformed URLs and literal local/private destinations.

    The HTTP executor must also validate resolved IPs and redirect destinations
    to enforce this boundary against DNS rebinding and redirects.
    """
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        host = url.hostname
        if (url.scheme != "https" or not host or url.username is not None
                or url.password is not None or url.port not in (None, 443)
                or "\\" in value):
            return False
        host = host.rstrip(".").lower()
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
                return False
            # Do not accept legacy numeric IP spellings or invalid DNS labels.
            if all(p.isdigit() for p in host.split(".")):
                return False
            return all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                       for p in host.split("."))
    except (ValueError, TypeError):
        return False


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}

    def start_task(self, task):
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        channels = set(re.findall(r"(?<![\w#])#[\w-]+", task.request_text))
        self.tasks[task.id] = (decision, channels)
        return decision

    def check(self, call):
        decision, channels = self.tasks.get(call.task_id, ("deny", set()))
        if decision != "allow":
            return "deny"
        if call.tool in PATH_TOOLS:
            path = call.args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            if not any(is_inside(path, root) for root in (WORK_DIR, DOCS_DIR)):
                return "deny"
            parts = path.lower().split("/")
            if any(p in {".git", ".ssh", ".env"} or p.startswith(".env.") for p in parts):
                return "deny"
            if call.tool != "fs.read" and (
                ".github" in parts or parts[-1] in {"license", "license.md", "license.txt", "codeowners", ".gitmodules"}
            ):
                return "require_approval"
            if call.tool == "fs.chmod":
                return "require_approval"
            # Do not permit deleting the project or docs root itself.
            if call.tool == "fs.delete" and path in (WORK_DIR, DOCS_DIR):
                return "deny"
            return "allow"
        if call.tool == "http.get":
            value = call.args.get("url")
            if not public_https_url(value):
                return "deny"
            hosts = self.config.get("allowed_hosts")
            if hosts is not None and urlsplit(value).hostname not in hosts:
                return "deny"
            return "allow"
        if call.tool == "notify.send":
            recipient = call.args.get("recipient")
            configured = self.config.get("allowed_recipients", ())
            return "allow" if recipient in channels or recipient in configured else "deny"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
