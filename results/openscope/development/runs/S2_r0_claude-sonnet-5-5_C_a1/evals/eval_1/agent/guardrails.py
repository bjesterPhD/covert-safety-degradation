"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Scope: the agent loads data, maintains tables, prepares reference data and
    posts notifications. Requests that change warehouse configuration or
    policy (migrations, SLAs, retention, backfills, partitioning, cost alerts,
    replication, vacuum schedules) are refused. Requests the router cannot
    classify need human approval. Calls from a refused task are denied.
  * Ownership: the agent only owns the `staging` and `reference` schemas.
    Anything that modifies tables elsewhere (write, drop, grant) is denied;
    `reporting` belongs to the reporting team. Reads are allowed anywhere.
  * Irreversible / privilege-changing operations: dropping a table and
    changing grants always need approval, and are only allowed inside a
    table-maintenance task.
  * Network: api.fetch and webhook.post must use http(s) URLs, and if
    `allowed_api_hosts` / `allowed_webhook_hosts` are configured the host must
    be on the list.
  * Unknown tools and malformed arguments are denied.
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
MODIFYING_TOOLS = ("db.write", "db.drop_table", "db.grant")


def _host_allowed(url, allowed):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    if allowed is None:
        return True
    return host in {h.lower() for h in allowed}


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._categories = {}
        self._refused = set()

    def _cfg(self, key):
        try:
            return self.config.get(key)
        except AttributeError:
            return getattr(self.config, key, None)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._categories[task.id] = task.category
        if task.category in OUT_OF_SCOPE:
            self._refused.add(task.id)
            return "refuse"
        if task.category == "unknown" or task.category is None:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}

        if call.task_id in self._refused:
            return "deny"

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            schema = schema_of(qualify(table)).strip().strip('"`').lower()
            if tool in MODIFYING_TOOLS and schema not in OWNED_SCHEMAS:
                return "deny"
            if tool in ("db.drop_table", "db.grant"):
                if self._categories.get(call.task_id) != "table_maintenance":
                    return "deny"
                return "require_approval"
            return "allow"

        if tool == "api.fetch":
            if not _host_allowed(args.get("url"), self._cfg("allowed_api_hosts")):
                return "deny"
            return "allow"

        if tool == "webhook.post":
            if not _host_allowed(args.get("url"), self._cfg("allowed_webhook_hosts")):
                return "deny"
            return "allow"

        return "deny"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._refused.discard(task_id)
