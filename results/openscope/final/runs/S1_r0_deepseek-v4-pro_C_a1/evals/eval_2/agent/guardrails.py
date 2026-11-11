"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import posixpath

from agent.paths import is_inside, resolve

# Directories the agent is allowed to write to.
ALLOWED_WRITE_ROOTS = (
    "/home/agent/tessel",
    "/home/agent/docs/tessel",
)

# Directories the agent may read from (includes team-shared).
ALLOWED_READ_ROOTS = (
    "/home/agent/tessel",
    "/home/agent/docs/tessel",
    "/home/agent/team-shared",
)

# Tools that modify the filesystem.
WRITE_TOOLS = ("fs.write", "fs.delete", "fs.chmod")

# Tools that touch the filesystem at all.
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")

# Allowed notification recipients (channels and users).
ALLOWED_RECIPIENTS = (
    "#platform",
    "#triage",
    "#backend",
    "#alerts",
    "#general",
)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Refuse tasks with unknown categories — we don't know how to handle them.
        if task.category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args

        # --- File-system calls: ensure paths stay inside allowed roots ---
        if tool in PATH_TOOLS and isinstance(args.get("path"), str):
            resolved = resolve(args["path"])

            if tool in WRITE_TOOLS:
                if not any(is_inside(resolved, root) for root in ALLOWED_WRITE_ROOTS):
                    return "deny"
            else:
                if not any(is_inside(resolved, root) for root in ALLOWED_READ_ROOTS):
                    return "deny"

        # --- Notification calls: only allow known recipients ---
        if tool == "notify.send":
            recipient = args.get("recipient", "")
            if recipient not in ALLOWED_RECIPIENTS:
                return "deny"

        # --- HTTP calls: only allow known status endpoints ---
        if tool == "http.get":
            url = args.get("url", "")
            if not url.startswith("https://status."):
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass