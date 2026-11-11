"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

ANALYTICS_SCHEMA = "analytics"
WORK_SCHEMA = "etl_work"
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
        table = call.args.get("table")
        if isinstance(table, str):
            # Never touch the reporting team's tables except to publish data.
            if table.startswith(ANALYTICS_SCHEMA + "."):
                if call.tool == "db.write":
                    return "allow"
                return "refuse"

            # Dropping temporary tables in our own schema is routine.
            if call.tool == "db.drop_table" and table.startswith(WORK_SCHEMA + ".tmp_"):
                return "allow"

        # Destructive operations need a second look.
        if call.tool in DESTRUCTIVE_TOOLS:
            return "require_approval"

        return "allow"

    def end_task(self, task_id):
        pass