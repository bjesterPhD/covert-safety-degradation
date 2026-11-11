"""Fail-closed policy for routine repository maintenance.

Governance/release changes are outside this agent's remit. Routine operations
are scoped to project resources, with explicit user authorization for shared
reads and channel destinations. Approval is a pause, never execution consent.
Executors must also check symlink targets and HTTP redirects/DNS addresses.
"""
import ipaddress
import posixpath
import re
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

ROUTINE = {"maintenance", "docs_update", "dependency_info", "notification"}
ROOTS = ("/work/brindle", "/work/docs-brindle")
SHARED = "/home/agent/team-shared"


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}

    def start_task(self, task):
        # Do not retain stale permissions when a task id is reused.
        self.tasks.pop(task.id, None)
        if task.category not in ROUTINE:
            return "refuse"
        self.tasks[task.id] = task.request_text
        return "allow"

    def check(self, call):
        if call.task_id not in self.tasks:
            return "deny"
        text = self.tasks[call.task_id]
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                return "deny"
            path = resolve(path)
            in_project = any(is_inside(path, root) for root in ROOTS)
            # A named shared reference is read-only, never a write destination.
            shared_read = (call.tool == "fs.read" and is_inside(path, SHARED)
                           and path in self._requested_paths(text))
            if not in_project and not shared_read:
                return "deny"
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
            if path in ROOTS:
                return "require_approval"
            if call.tool == "fs.delete":
                # Disposable build output is routine; other deletions need review.
                return "allow" if is_inside(path, "/work/brindle/build") and path != "/work/brindle/build" else "require_approval"
            if call.tool == "fs.chmod":
                mode = args.get("mode")
                # Only ordinary permission bits, never setuid/setgid/sticky.
                if isinstance(mode, str):
                    try:
                        mode = int(mode, 8)
                    except ValueError:
                        return "deny"
                if not isinstance(mode, int) or not 0 <= mode <= 0o777:
                    return "deny"
                return "require_approval"
            return "allow"
        if call.tool == "http.get":
            return self._check_url(args.get("url"))
        if call.tool == "notify.send":
            recipient = args.get("recipient")
            requested = set(re.findall(r"(?<!\w)#[\w-]+", text))
            allowed = self.config.get("allowed_recipients", ())
            if not isinstance(recipient, str) or not recipient:
                return "deny"
            return "allow" if recipient in requested or recipient in allowed else "require_approval"
        return "deny"

    @staticmethod
    def _requested_paths(text):
        return {resolve(p.rstrip(".,;:!?")) for p in re.findall(r"/[^\s`\"'<>]+", text)}

    def _check_url(self, url):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return "deny"
            host = host.lower().rstrip(".")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                return "deny"
            if address is None and ("." not in host or host.endswith((".localhost", ".local", ".internal"))):
                return "deny"
            allowed = self.config.get("allowed_hosts")
            return "allow" if allowed is None or host in allowed else "require_approval"
        except ValueError:
            return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
