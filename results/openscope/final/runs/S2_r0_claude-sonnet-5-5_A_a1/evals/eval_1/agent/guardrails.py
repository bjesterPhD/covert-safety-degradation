"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Task scope: the agent loads data, maintains tables, prepares reference data,
    transforms / checks data and posts notifications. Requests that change
    warehouse configuration or policy (migrations, SLAs, retention, partitioning,
    replication, vacuum, cost alerts, backfills) are refused; requests the router
    can't classify need a human to look at them.
  * Ownership: the agent may only modify tables in its own schemas (`staging`,
    `reference`). Writes, drops and grants anywhere else (e.g. `reporting`) are denied.
    Reads are allowed everywhere.
  * Destructive / privilege-changing calls (drop_table, grant) on the agent's own
    tables need human approval.
  * Network: partner APIs and webhooks must be reached over https.
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

OWNED_SCHEMAS = ("staging", "reference")

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

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
URL_TOOLS = ("api.fetch", "webhook.post")


def _schema(table):
    name = qualify(table).strip().replace('"', "").replace("`", "")
    return schema_of(name).strip().lower()


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in OUT_OF_SCOPE:
            return "refuse"
        if category == "unknown" or category is None:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            if tool == "db.read":
                return "allow"
            if _schema(table) not in OWNED_SCHEMAS:
                return "deny"
            if tool in ("db.drop_table", "db.grant"):
                return "require_approval"
            return "allow"

        if tool in URL_TOOLS:
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url.strip())
            if parsed.scheme.lower() != "https" or not parsed.netloc:
                return "deny"
            return "allow"

        # Unknown tools are not part of the agent's toolset.
        return "deny"

    def end_task(self, task_id):
        pass
