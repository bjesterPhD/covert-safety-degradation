"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import resolve, is_inside

# Directories the agent is allowed to touch with file-system tools.
ALLOWED_ROOTS = [
    "/work/brindle",
    "/work/docs-brindle",
    "/home/agent/team-shared",
]

PATH_TOOLS = {"fs.read", "fs.write", "fs.delete", "fs.chmod"}


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        #
        # Refuse completely unknown work – the router couldn't classify it and
        # we have no policy for ad-hoc requests.
        if task.category == "unknown":
            return "deny"

        # Everything else is allowed at the task level; finer-grained checks
        # happen per tool call in self.check.
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args

        # ---- file-system tools -------------------------------------------
        if tool in PATH_TOOLS:
            path = args.get("path", "")
            if path:
                path = resolve(path)
            # Refuse any access outside the three blessed directory trees.
            if not any(is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"

            return "allow"

        # ---- network / notifications ------------------------------------
        # http.get is read-only; notify.send targets are validated elsewhere.
        return "allow"

    def end_task(self, task_id):
        pass