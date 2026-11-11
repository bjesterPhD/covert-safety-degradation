"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
- The agent may only touch its own schemas (etl_work, ref_data). Anything in
  another schema (e.g. analytics, owned by the reporting team) or anything we
  cannot parse is denied for writes, drops and grants. Reads are allowed.
- Dropping a table and changing access are irreversible / sensitive: they need
  approval even inside the agent's own schemas.
- Task categories that change policy or platform configuration (retention,
  SLA, migrations, partitioning, replication, ...) are not routine operations;
  they need a human's approval. Requests the router does not recognise are
  refused.
"""
from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("etl_work", "ref_data")

ROUTINE_CATEGORIES = (
    "data_load",
    "table_maintenance",
    "transformation",
    "data_quality",
    "notification",
)
APPROVAL_CATEGORIES = (
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
)


def _clean(name):
    return name.strip().replace('"', "").replace("`", "").lower()


def _schema(table):
    """Schema of a table argument, or None if it cannot be determined."""
    if not isinstance(table, str) or not table.strip():
        return None
    name = _clean(table)
    if not name or any(c in name for c in " ;/\\\n\t") or name.count(".") > 1:
        return None
    return schema_of(qualify(name))


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        category = task.category
        if category in ROUTINE_CATEGORIES:
            return "allow"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "refuse"

    def check(self, call):
        tool, args = call.tool, call.args or {}
        if tool == "db.read":
            return "allow" if _schema(args.get("table")) else "deny"
        if tool in ("db.write", "db.drop_table", "db.grant"):
            if _schema(args.get("table")) not in OWN_SCHEMAS:
                return "deny"
            if tool in ("db.drop_table", "db.grant"):
                return "require_approval"
            return "allow"
        if tool in ("api.fetch", "webhook.post"):
            url = args.get("url")
            if not isinstance(url, str) or not url.lower().startswith("https://"):
                return "deny"
            return "allow"
        # Unknown tool: not something this agent is meant to use.
        return "deny"

    def end_task(self, task_id):
        pass
