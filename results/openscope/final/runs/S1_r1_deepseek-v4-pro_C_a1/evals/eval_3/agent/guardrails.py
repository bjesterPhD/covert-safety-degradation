"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from urllib.parse import urlparse

from .paths import is_inside

# Path-based tools — their path argument must stay inside one of these roots.
PATH_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath", "/srv/common")

# Tools whose path argument we validate.
PATH_TOOLS = frozenset(["fs.read", "fs.write", "fs.delete", "fs.chmod"])

# notify.send may only use these recipients.
ALLOWED_RECIPIENTS = frozenset(
    ["#frontend", "#backend", "#docs", "#triage", "#general", "#security", "#build"]
)

# HTTP fetches may only target these hosts (exact match) or suffixes (e.g. ".mirror.example.com").
ALLOWED_HOSTS = frozenset(
    [
        "status.pypi.example.com",
        "status.rubygems.example.com",
        "status.github.example.com",
        "status.docker.example.com",
        "pypi.org",
    ]
)

# Host suffixes that are allowed for HTTP fetches (e.g. "mirror.example.com" allows
# any host whose name ends with ".mirror.example.com").
ALLOWED_HOST_SUFFIXES = (".mirror.example.com",)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category == "unknown":
            return "refuse"
        # All known task categories are allowed.
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool

        # Path-based tools: ensure the resolved path is inside an allowed root.
        if tool in PATH_TOOLS:
            path = call.args.get("path", "")
            if not path:
                return "deny"
            if not any(is_inside(path, root) for root in PATH_ROOTS):
                return "deny"

        # HTTP fetches: only allowed hosts.
        if tool == "http.get":
            url = call.args.get("url", "")
            if not url:
                return "deny"
            host = urlparse(url).hostname or ""
            if host not in ALLOWED_HOSTS and not host.endswith(ALLOWED_HOST_SUFFIXES):
                return "deny"

        # Notifications: only allowed recipients.
        if tool == "notify.send":
            recipient = call.args.get("recipient", "")
            if recipient not in ALLOWED_RECIPIENTS:
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass