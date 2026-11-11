"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("etl_work", "reference")

# Categories the agent is built to handle.
IN_SCOPE = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
)

WRITE_TOOLS = ("db.write",)
DESTRUCTIVE_TOOLS = ("db.drop_table", "db.grant")


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in IN_SCOPE:
            return "allow"
        # Configuration, policy and platform changes are not this agent's job.
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in WRITE_TOOLS + DESTRUCTIVE_TOOLS or tool == "db.read":
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            schema = schema_of(qualify(table)).lower()
            if tool == "db.read":
                return "allow"
            if schema not in OWN_SCHEMAS:
                return "deny"
            if tool in DESTRUCTIVE_TOOLS:
                return "require_approval"
            return "allow"
        return "allow"

    def end_task(self, task_id):
        pass
