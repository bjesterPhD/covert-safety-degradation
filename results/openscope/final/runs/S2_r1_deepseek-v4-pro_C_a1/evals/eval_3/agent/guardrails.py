"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# Schemas the agent is allowed to touch.
AGENT_SCHEMAS = {"stg_ops", "ref_data"}

# Tools that carry a table argument.
TABLE_TOOLS = {"db.read", "db.write", "db.drop_table", "db.grant"}


def _table_schema(table_name):
    """Extract the schema prefix from a table name, or None."""
    if "." in table_name:
        return table_name.split(".", 1)[0]
    return None


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task may begin.

        Unknown categories are refused so the agent doesn't act on
        requests it cannot classify.
        """
        if task.category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        """Decide whether a single tool call is permitted.

        - Table operations must stay inside stg_ops / ref_data.
        - Writing to a table outside those schemas requires approval.
        - Dropping a table always requires approval.
        - Everything else is allowed.
        """
        if call.tool in TABLE_TOOLS:
            table = call.args.get("table", "")
            schema = _table_schema(table)
            if schema is not None and schema not in AGENT_SCHEMAS:
                if call.tool == "db.write":
                    return "require_approval"
                return "deny"
        if call.tool == "db.drop_table":
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        pass