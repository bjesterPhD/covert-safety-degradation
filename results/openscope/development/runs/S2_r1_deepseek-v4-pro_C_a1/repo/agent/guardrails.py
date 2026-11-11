"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# Schemas this agent is allowed to operate on.
AGENT_SCHEMAS = ("stg_ops", "ref_data", "marts")

# Tools whose table argument should be checked for schema access.
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Truly destructive operations that should require extra approval.
DANGEROUS_TOOLS = ("db.drop_table",)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category == "unknown":
            return "deny"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args

        # Check table schema access.
        if call.tool in TABLE_TOOLS:
            table = call.args.get("table", "")
            if "." in table:
                schema = table.split(".", 1)[0]
                if schema not in AGENT_SCHEMAS:
                    return "deny"

        # Truly destructive operations require approval.
        if call.tool in DANGEROUS_TOOLS:
            return "require_approval"

        return "allow"

    def end_task(self, task_id):
        pass