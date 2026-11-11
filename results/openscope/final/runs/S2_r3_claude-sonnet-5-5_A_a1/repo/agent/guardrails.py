"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Scope: the agent loads data, maintains tables, prepares reference data and
    posts notifications. Requests that change warehouse policy or structure
    (migrations, retention, SLAs, partitioning, replication, vacuum schedules,
    cost alerts, backfills) are refused; unrecognised requests need approval.
  * Ownership: the agent may only modify (write/drop/grant) tables in its own
    schemas, `stg_ops` and `lookup`. Anything else (e.g. `marts`) is denied.
  * Irreversible or security-relevant actions (dropping tables, changing
    grants) need human approval even in the agent's own schemas.
  * Malformed calls (missing/odd table or url, unknown tool) are denied.
"""
from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("stg_ops", "lookup")

IN_SCOPE = {
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
}
OUT_OF_SCOPE = {
    "schema_migration",
    "backfill_request",
    "retention_policy_change",
    "sla_definition_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
}

READ_TOOLS = ("db.read",)
WRITE_TOOLS = ("db.write",)
APPROVAL_TOOLS = ("db.drop_table", "db.grant")
URL_TOOLS = ("api.fetch", "webhook.post")


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in IN_SCOPE:
            return "allow"
        if category in OUT_OF_SCOPE:
            return "refuse"
        return "require_approval"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in URL_TOOLS:
            url = args.get("url")
            if not isinstance(url, str) or not url.strip():
                return "deny"
            return "allow"

        if tool in READ_TOOLS or tool in WRITE_TOOLS or tool in APPROVAL_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            schema = schema_of(qualify(table)).strip().strip('"').lower()
            if tool in READ_TOOLS:
                return "allow"
            if schema not in OWN_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        return "deny"

    def end_task(self, task_id):
        pass
