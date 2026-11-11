"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
- The agent only does routine operations: loads, table maintenance, reference
  data, transformations, data-quality checks and notifications. Requests that
  change warehouse policy or configuration (retention, SLAs, migrations,
  backfills, partitioning, cost alerts, replication, vacuum schedules) are
  refused as out of scope for this agent; so are requests we cannot categorise.
- Writes, drops and grants may only touch the agent's own schemas
  (etl_work, ref_data). Anything in another schema (e.g. analytics, owned by
  the reporting team) is denied. Reads of other schemas are allowed.
- Destructive or access-changing calls (drop_table, grant) need approval even
  in the agent's own schemas.
- Table calls without a usable table argument are denied.
"""
from agent.paths import qualify, schema_of

AGENT_SCHEMAS = ("etl_work", "ref_data")

OUT_OF_SCOPE = {
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
}

APPROVAL_TOOLS = ("db.drop_table", "db.grant")
MUTATING_TOOLS = ("db.write", "db.drop_table", "db.grant")


class Policy:
    def __init__(self, config=None):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in OUT_OF_SCOPE or task.category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        if not tool.startswith("db."):
            return "allow"
        table = (call.args or {}).get("table")
        if not isinstance(table, str) or not table.strip():
            return "deny"
        schema = schema_of(qualify(table)).strip().strip('"').lower()
        if tool in MUTATING_TOOLS and schema not in AGENT_SCHEMAS:
            return "deny"
        if tool in APPROVAL_TOOLS:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        pass
