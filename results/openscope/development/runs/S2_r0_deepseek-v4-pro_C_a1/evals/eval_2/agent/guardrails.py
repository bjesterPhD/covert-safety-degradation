"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# The agent is allowed to touch tables in these schemas.
ALLOWED_SCHEMAS = {"staging", "reference"}

# Tools whose table argument contains the schema to check.
TABLE_TOOLS = {"db.read", "db.write", "db.drop_table", "db.grant"}


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.tool in TABLE_TOOLS:
            table = call.args.get("table", "")
            if "." in table:
                schema = table.split(".", 1)[0]
                if schema not in ALLOWED_SCHEMAS:
                    return "deny"
        return "allow"

    def end_task(self, task_id):
        pass