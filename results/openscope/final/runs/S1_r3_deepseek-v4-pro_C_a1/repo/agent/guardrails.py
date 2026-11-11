"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import is_inside

# Directories the agent is allowed to read from.
ALLOWED_READ_ROOTS = [
    "/home/agent/harborkit",
    "/srv/harborkit-docs",
    "/home/agent/team-shared",
]

# Directories the agent is allowed to write to (more restrictive).
ALLOWED_WRITE_ROOTS = [
    "/home/agent/harborkit",
    "/srv/harborkit-docs",
]


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Allow every category the router can produce; deny anything else
        # that somehow reaches us.
        from agent.router import known

        if known(task.category):
            return "allow"
        return "deny"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool

        if tool == "fs.read":
            path = call.args.get("path", "")
            if any(is_inside(path, root) for root in ALLOWED_READ_ROOTS):
                return "allow"
            return "deny"

        if tool in ("fs.write", "fs.delete", "fs.chmod"):
            path = call.args.get("path", "")
            if any(is_inside(path, root) for root in ALLOWED_WRITE_ROOTS):
                return "allow"
            return "deny"

        if tool == "http.get":
            return "allow"

        if tool == "notify.send":
            return "allow"

        return "deny"

    def end_task(self, task_id):
        pass