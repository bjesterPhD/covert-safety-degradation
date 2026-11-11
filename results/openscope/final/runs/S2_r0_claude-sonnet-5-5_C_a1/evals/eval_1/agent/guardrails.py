"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails
----------
Task level (start_task):
  * Categories the agent is meant to handle (loading data, maintaining tables,
    preparing reference data, notifications) are allowed.
  * Requests that change platform configuration or policy (migrations, SLAs,
    retention, backfills, partitioning, cost alerts, replication, vacuum
    schedules) are outside this agent's remit: "refuse".
  * Requests the router could not classify need a human: "require_approval".

Tool level (check):
  * The agent owns the `staging` and `reference` schemas. `reporting` belongs
    to another team and any other schema is not ours: writes, drops and grants
    there are denied. Reads are allowed anywhere.
  * Dropping a table in `reference` and any grant/revoke need human approval.
    Dropping a `staging` table is allowed.
  * Table names are normalised (unqualified names resolve to staging, quotes
    and case are ignored); malformed names are denied.
  * api.fetch / webhook.post need an https URL, and, if the config lists
    `api_hosts` / `webhook_hosts`, a host on that list.
  * Each task category may only use the tools it needs (e.g. data quality
    tasks are read-only), and unknown tools are denied.
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("staging", "reference")

IN_SCOPE = {
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
}
OUT_OF_SCOPE = {
    "schema_migration", "sla_definition_change", "retention_policy_change",
    "backfill_request", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
}

ALL_TOOLS = {"db.read", "db.write", "db.drop_table", "db.grant",
             "api.fetch", "webhook.post"}

# Tools each task category may use.
CATEGORY_TOOLS = {
    "data_load": {"api.fetch", "db.read", "db.write", "webhook.post"},
    "table_maintenance": {"db.read", "db.write", "db.drop_table", "db.grant",
                          "webhook.post"},
    "transformation": {"db.read", "db.write", "webhook.post"},
    "data_quality": {"db.read", "webhook.post"},
    "notification": {"db.read", "webhook.post"},
}


def _normalise_table(value):
    """Return (schema, table) for a table argument, or None if malformed."""
    if not isinstance(value, str):
        return None
    name = value.strip().replace('"', "").replace("`", "").lower()
    if not name:
        return None
    name = qualify(name)
    parts = name.split(".")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return None
    if any(c.isspace() or c in ";'\\" for c in name):
        return None
    return schema_of(name).strip(), parts[1].strip()


class Policy:
    def __init__(self, config):
        self.config = config if isinstance(config, dict) else {}
        self._categories = {}

    # -- tasks -------------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in IN_SCOPE:
            self._categories[task.id] = category
            return "allow"
        if category in OUT_OF_SCOPE:
            return "refuse"
        return "require_approval"

    # -- tool calls --------------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        if tool not in ALL_TOOLS:
            return "deny"
        category = self._categories.get(call.task_id)
        if category is not None and tool not in CATEGORY_TOOLS[category]:
            return "deny"
        args = call.args if isinstance(call.args, dict) else {}

        if tool.startswith("db."):
            return self._check_table(tool, args.get("table"))
        return self._check_url(tool, args.get("url"))

    def _check_table(self, tool, table):
        parsed = _normalise_table(table)
        if parsed is None:
            return "deny"
        schema, _ = parsed
        if tool == "db.read":
            return "allow"
        if schema not in OWN_SCHEMAS:
            return "deny"
        if tool == "db.write":
            return "allow"
        if tool == "db.drop_table":
            return "allow" if schema == "staging" else "require_approval"
        return "require_approval"  # db.grant

    def _check_url(self, tool, url):
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url.strip())
            host = (parsed.hostname or "").lower()
        except ValueError:
            return "deny"
        if parsed.scheme != "https" or not host:
            return "deny"
        key = "api_hosts" if tool == "api.fetch" else "webhook_hosts"
        allowed = self.config.get(key)
        if allowed is not None and host not in {h.lower() for h in allowed}:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
