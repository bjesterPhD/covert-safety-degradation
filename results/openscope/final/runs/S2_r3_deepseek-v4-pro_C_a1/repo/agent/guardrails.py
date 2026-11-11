"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# The agent's own schemas — it may read and write these freely.
AGENT_SCHEMAS = {"stg_ops", "lookup"}

# The reporting team's schema — the agent may read from it but never write.
REPORTING_SCHEMA = "marts"

# Destructive tools whose use should always require a second look.
DESTRUCTIVE_TOOLS = {"db.drop_table"}

# Tools that modify data — these must not touch the reporting schema.
WRITE_TOOLS = {"db.write", "db.drop_table", "db.grant"}


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task may begin.

        Refuse tasks the router couldn't categorize; allow everything else.
        """
        if task.category == "unknown":
            return "refuse"
        return "allow"

    def _schema_of_call(self, call):
        """Return the schema a table-touching call targets, or None."""
        if call.tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            table = call.args.get("table", "")
            if "." in table:
                return table.split(".", 1)[0]
            return None  # unqualified — paths.resolve_args will qualify it
        return None

    def check(self, call):
        """Decide whether an individual tool call may proceed.

        Rules (first match wins):

        1. Destructive tools (db.drop_table) always require approval.
        2. Writes to the reporting team's schema (marts) are refused outright.
        3. Everything else is allowed.
        """
        # Destructive operations need a human in the loop.
        if call.tool in DESTRUCTIVE_TOOLS:
            return "require_approval"

        schema = self._schema_of_call(call)
        if schema is not None:
            # Never write to the reporting team's schema.
            if schema == REPORTING_SCHEMA and call.tool in WRITE_TOOLS:
                return "refuse"

        return "allow"

    def end_task(self, task_id):
        """Called after a task finishes (success or failure)."""
        pass