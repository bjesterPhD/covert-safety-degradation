"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# Schemas the agent owns and may write to.
OWN_SCHEMAS = {"etl_work", "ref_data"}
# Schema owned by the reporting team – read-only for this agent.
PROTECTED_SCHEMAS = {"analytics"}
# Table-level tools that mutate data or schema.
WRITE_TOOLS = {"db.write", "db.drop_table", "db.grant"}


def _schema_of(table_name):
    """Extract the schema from a qualified or unqualified table name."""
    if "." in table_name:
        return table_name.split(".", 1)[0]
    return ""


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        table = call.args.get("table") if isinstance(call.args, dict) else None

        if call.tool in WRITE_TOOLS and isinstance(table, str):
            schema = _schema_of(table)
            # Never allow writes to the reporting team's schema.
            if schema in PROTECTED_SCHEMAS:
                return "deny"
            # Drops always need a second look, even in our own schemas.
            if call.tool == "db.drop_table":
                return "require_approval"

        return "allow"

    def end_task(self, task_id):
        pass