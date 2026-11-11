"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
- The agent only does loading, table maintenance, reference data, notifications,
  transformations and data-quality checks. Requests that change warehouse
  configuration (migrations, SLAs, retention, ...) are refused; requests we
  cannot classify need a human to approve.
- Tool calls may only touch the agent's own schemas (staging, reference).
  Anything else (e.g. `reporting`) is denied.
- Destructive or access-changing calls (drop, grant) need approval.
"""

from agent.paths import qualify, schema_of

AGENT_SCHEMAS = ("staging", "reference")
IN_SCOPE = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
)
APPROVAL_TOOLS = ("db.drop_table", "db.grant")
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")


def _schema(table):
    name = qualify(str(table)).replace('"', "").replace("`", "")
    return schema_of(name).strip().lower()


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in IN_SCOPE:
            return "allow"
        if task.category == "unknown":
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            if _schema(table) not in AGENT_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"
        if tool in ("api.fetch", "webhook.post"):
            return "allow"
        return "deny"

    def end_task(self, task_id):
        pass
