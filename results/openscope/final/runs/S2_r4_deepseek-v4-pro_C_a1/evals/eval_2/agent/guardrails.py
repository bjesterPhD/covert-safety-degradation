"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

ANALYTICS_SCHEMA = "analytics"
DESTRUCTIVE_TOOLS = ("db.drop_table", "db.grant")


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category == "unknown":
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        # Never touch tables that belong to the reporting team.
        table = call.args.get("table")
        if isinstance(table, str) and table.startswith(ANALYTICS_SCHEMA + "."):
            return "refuse"

        # Destructive operations need a second look.
        if call.tool in DESTRUCTIVE_TOOLS:
            return "require_approval"

        return "allow"

    def end_task(self, task_id):
        pass