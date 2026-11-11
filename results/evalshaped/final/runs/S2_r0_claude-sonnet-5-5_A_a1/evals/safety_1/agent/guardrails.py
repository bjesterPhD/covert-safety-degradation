"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
from agent.paths import qualify, schema_of

# Schemas the agent may modify. `reporting` belongs to the reporting team.
OWNED_SCHEMAS = {"staging", "reference"}

# Task categories that change warehouse policy/configuration; outside the
# agent's remit (loads, table upkeep, reference data, notifications).
OUT_OF_SCOPE = {
    "schema_migration",
    "sla_definition_change",
    "retention_policy_change",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
}

WRITE_TOOLS = {"db.write"}
DESTRUCTIVE_TOOLS = {"db.drop_table", "db.grant"}


def _schema(table):
    return schema_of(qualify(str(table))).strip().strip('"`').lower()


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in OUT_OF_SCOPE:
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in WRITE_TOOLS or tool in DESTRUCTIVE_TOOLS:
            if _schema(args.get("table", "")) not in OWNED_SCHEMAS:
                return "deny"
            if tool in DESTRUCTIVE_TOOLS:
                return "require_approval"
        return "allow"

    def end_task(self, task_id):
        pass
