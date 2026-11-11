"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Task scope: the agent does routine operations only (loads, table
    maintenance, transformations, data-quality checks, notifications).
    Requests that change warehouse configuration or structure (migrations,
    partitioning, retention, SLAs, replication, ...) are refused, as are
    requests the router cannot classify.
  * Schema ownership: the agent may not modify tables outside its own schemas
    (etl_work, reference); `analytics` belongs to the reporting team.
    Reading other schemas is allowed.
  * Destructive / privilege operations: dropping tables or granting access is
    only automatic in etl_work; elsewhere in the agent's schemas it requires
    approval.
  * Unknown tools are denied.
"""

from agent.paths import qualify, schema_of
from agent.router import UNKNOWN

ALLOWED_CATEGORIES = {
    "data_load",
    "table_maintenance",
    "transformation",
    "data_quality",
    "notification",
}
OWN_SCHEMAS = {"etl_work", "reference"}
KNOWN_TOOLS = {
    "db.read", "db.write", "db.drop_table", "db.grant", "api.fetch", "webhook.post",
}


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category == UNKNOWN or task.category not in ALLOWED_CATEGORIES:
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        if tool not in KNOWN_TOOLS:
            return "deny"
        if tool == "db.read":
            return "allow"
        if tool.startswith("db."):
            table = (call.args or {}).get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            schema = schema_of(qualify(table)).strip().lower().strip('"')
            if schema not in OWN_SCHEMAS:
                return "deny"
            if tool in ("db.drop_table", "db.grant") and schema != "etl_work":
                return "require_approval"
        return "allow"

    def end_task(self, task_id):
        pass
