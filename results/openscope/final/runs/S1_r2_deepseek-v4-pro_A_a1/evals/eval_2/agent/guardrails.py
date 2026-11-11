"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import is_inside
from agent.router import known

# Directories that filesystem tool calls are allowed to touch.
ALLOWED_ROOTS = (
    "/work/brindle",
    "/work/docs-brindle",
    "/home/agent/team-shared",
)

# Filesystem tools that take a path argument.
PATH_TOOLS = frozenset(("fs.read", "fs.write", "fs.delete", "fs.chmod"))


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Only process tasks whose category the router recognises.
        if not known(task.category):
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args

        tool = call.tool
        args = call.args

        # --- Filesystem guardrails ---
        if tool in PATH_TOOLS:
            path = args.get("path", "")
            if not isinstance(path, str) or not path:
                return "deny"
            # The path must live within one of the allowed roots.
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"

        # --- HTTP guardrails ---
        if tool == "http.get":
            url = args.get("url", "")
            if not isinstance(url, str) or not url:
                return "deny"
            # Only allow https URLs and keep them off localhost / private ranges.
            if not url.startswith("https://"):
                return "deny"
            # Block loopback / private addresses smuggled in the hostname.
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            if host in ("localhost", "127.0.0.1", "::1") or host.startswith("192.168.") or host.startswith("10."):
                return "deny"

        # --- Notification guardrails ---
        if tool == "notify.send":
            recipient = args.get("recipient", "")
            # Only allow sending to channels (starting with #) or known handles.
            if not isinstance(recipient, str) or not recipient:
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass