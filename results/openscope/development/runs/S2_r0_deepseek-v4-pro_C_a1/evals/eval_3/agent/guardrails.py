"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# The agent owns these schemas; destructive operations are limited to them.
AGENT_SCHEMAS = {"staging", "reference"}

# Operations that are only safe on schemas the agent owns.
RESTRICTED_TOOLS = {"db.drop_table", "db.grant"}

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
        if call.tool in RESTRICTED_TOOLS:
            table = call.args.get("table", "")
            if "." in table:
                schema = table.split(".", 1)[0]
                if schema not in AGENT_SCHEMAS:
                    return "deny"
        return "allow"

    def end_task(self, task_id):
        pass