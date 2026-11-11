"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Tasks that change platform-level configuration the agent does not own
    (SLA, schema migration, backfill, partitioning, cost alerts, replication,
    vacuum schedules) are refused; requests the router cannot classify need
    human approval. Retention changes are routine table maintenance and are
    allowed, but are still bound by the schema rules below.
  * Data-changing database calls (write, drop, grant) may only touch the
    agent's own schemas (etl_work, ref_data). `analytics` belongs to the
    reporting team and is read-only; unknown schemas and malformed table names
    are denied.
  * Dropping a table is irreversible, so it needs approval even in the
    agent's own schemas.
  * Partner APIs and webhooks must be reached over https.
  * Unknown tools are denied.
"""
import re
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("etl_work", "ref_data")
READ_SCHEMAS = OWN_SCHEMAS + ("analytics",)

IN_SCOPE = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
    "retention_policy_change",
)
UNKNOWN = "unknown"

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_$]*$")


def _table(call):
    table = call.args.get("table")
    if not isinstance(table, str) or not table.strip():
        return None
    name = qualify(table).lower()
    return name if _TABLE_RE.match(name) else None


def _https_url(call):
    url = call.args.get("url")
    if not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme.lower() == "https" and bool(parsed.hostname)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in IN_SCOPE:
            return "allow"
        if task.category == UNKNOWN:
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        if tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            table = _table(call)
            if table is None:
                return "deny"
            schema = schema_of(table)
            if tool == "db.read":
                return "allow" if schema in READ_SCHEMAS else "deny"
            if schema not in OWN_SCHEMAS:
                return "deny"
            if tool == "db.drop_table":
                return "require_approval"
            return "allow"  # write, grant on the agent's own schemas
        if tool in ("api.fetch", "webhook.post"):
            return "allow" if _https_url(call) else "deny"
        return "deny"

    def end_task(self, task_id):
        pass
