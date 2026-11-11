"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrail decisions implemented here
----------------------------------
* Routine warehouse operations are allowed without approval:
  ``data_load``, ``table_maintenance``, ``transformation``, ``data_quality``,
  and ``notification``.
* Other known change-management categories require a human approval before the
  task starts (for example retention or SLA changes).
* Unknown categories are refused.
* The agent may only touch tables in its own schemas (``etl_work`` and
  ``ref_data``).  Tables in ``analytics`` (or anywhere else) are denied, as are
  calls with missing or malformed table names.
* Destructive or privilege-changing table operations (``db.drop_table`` and
  ``db.grant``) require approval even inside the agent's schemas.
* Network calls (``api.fetch`` and ``webhook.post``) must use a URL.  HTTPS is
  allowed, plain HTTP requires approval, and anything else is denied.
"""

from urllib.parse import urlparse

ROUTINE_CATEGORIES = {
    "data_load",
    "table_maintenance",
    "transformation",
    "data_quality",
    "notification",
}

APPROVAL_CATEGORIES = {
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
}

ALLOWED_TOOLS = {
    "db.read",
    "db.write",
    "db.drop_table",
    "db.grant",
    "api.fetch",
    "webhook.post",
}

TABLE_TOOLS = {"db.read", "db.write", "db.drop_table", "db.grant"}
APPROVAL_TABLE_TOOLS = {"db.drop_table", "db.grant"}
DEFAULT_SCHEMAS = {"etl_work", "ref_data"}


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.allowed_schemas = set(
            self.config.get("allowed_schemas", DEFAULT_SCHEMAS)
        )
        self.allowed_hosts = set(self.config.get("allowed_hosts") or [])
        self._task_categories = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        task_id = getattr(task, "id", None)
        if task_id is not None:
            self._task_categories[task_id] = category

        if category in ROUTINE_CATEGORIES:
            return "allow"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool not in ALLOWED_TOOLS:
            return "deny"

        if tool in TABLE_TOOLS:
            return self._check_table_call(tool, args)

        return self._check_url_call(tool, args)

    def _check_table_call(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return "deny"

        # Arguments are normally resolved before this point, but a defensive
        # caller may still pass an unqualified name; treat it as etl_work.
        if "." in table:
            schema = table.split(".", 1)[0]
        else:
            schema = "etl_work"

        if schema not in self.allowed_schemas:
            return "deny"

        if tool in APPROVAL_TABLE_TOOLS:
            return "require_approval"
        return "allow"

    def _check_url_call(self, tool, args):
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return "deny"

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()

        if self.allowed_hosts and hostname not in self.allowed_hosts:
            return "deny"

        if scheme == "https":
            return "allow"
        if scheme == "http":
            return "require_approval"
        return "deny"

    def end_task(self, task_id):
        self._task_categories.pop(task_id, None)
