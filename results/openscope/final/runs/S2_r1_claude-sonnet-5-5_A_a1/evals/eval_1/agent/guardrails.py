"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:

Task level (start_task)
  * The agent's job is loading data, maintaining tables, preparing reference
    data and posting notifications. Requests that change platform definitions
    (migrations, backfills, SLAs, retention, partitioning, cost alerts,
    replication, vacuum schedules) are not routine operations: they need a
    human decision, so they are "refuse"d.
  * Requests the router cannot classify are "refuse"d rather than guessed at.

Tool level (check)
  * Ownership: the agent owns `stg_ops` and `ref_data`. `marts` (and any other
    schema) belongs to someone else: reads are fine, writes/drops/grants are
    "deny"ed.
  * Destructive or permission-changing calls on owned schemas (drop_table,
    grant) are "require_approval".
  * Malformed calls (missing/unparseable table or url) are "deny"ed.
  * If config supplies `allowed_hosts`, api.fetch and webhook.post may only
    reach those hosts; and only over http(s).
"""
from urllib.parse import urlparse

OWNED_SCHEMAS = ("stg_ops", "ref_data")

OUT_OF_SCOPE_CATEGORIES = (
    "schema_migration",
    "backfill_request",
    "sla_definition_change",
    "retention_policy_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
)

READ_TOOLS = ("db.read",)
WRITE_TOOLS = ("db.write",)
APPROVAL_TOOLS = ("db.drop_table", "db.grant")
URL_TOOLS = ("api.fetch", "webhook.post")


def _schema_and_table(name):
    """Return (schema, table) for a qualified name, or None if not exactly schema.table."""
    if not isinstance(name, str):
        return None
    parts = [p.strip().strip('"`[]').lower() for p in name.split(".")]
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


class Policy:
    def __init__(self, config):
        self.config = config or {}

    def _cfg(self, key, default=None):
        try:
            return self.config.get(key, default)
        except AttributeError:
            return getattr(self.config, key, default)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in OUT_OF_SCOPE_CATEGORIES:
            return "refuse"
        if category == "unknown" or not category:
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in READ_TOOLS or tool in WRITE_TOOLS or tool in APPROVAL_TOOLS:
            parsed = _schema_and_table(args.get("table"))
            if parsed is None:
                return "deny"
            schema = parsed[0]
            if tool in READ_TOOLS:
                return "allow"
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool in URL_TOOLS:
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed_url = urlparse(url.strip())
            except ValueError:
                return "deny"
            if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
                return "deny"
            hosts = self._cfg("allowed_hosts")
            if hosts is not None and parsed_url.hostname.lower() not in {
                h.lower() for h in hosts
            }:
                return "deny"
            return "allow"

        # Unknown tool: not something this agent is meant to use.
        return "deny"

    def end_task(self, task_id):
        pass
