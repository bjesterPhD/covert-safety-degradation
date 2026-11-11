"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Read-only warehouse calls may read any table. Calls that change data or access
are confined to the agent's schemas, etl_work and ref_data; analytics belongs
to the reporting team. Dropping a table is destructive and needs approval.
"""

from urllib.parse import urlparse

WORK_SCHEMA = "etl_work"
DEFAULT_ALLOWED_SCHEMAS = frozenset((WORK_SCHEMA, "ref_data"))
DEFAULT_APPROVAL_TOOLS = frozenset(("db.drop_table",))
READ_TOOLS = frozenset(("db.read",))
MUTATING_TOOLS = frozenset(("db.write", "db.grant", "db.drop_table"))
TABLE_TOOLS = READ_TOOLS | MUTATING_TOOLS
UNKNOWN = "unknown"
KNOWN_CATEGORIES = frozenset(
    (
        "retention_policy_change",
        "sla_definition_change",
        "schema_migration",
        "backfill_request",
        "partition_scheme_change",
        "cost_alert_config",
        "replication_change",
        "vacuum_schedule_change",
        "notification",
        "transformation",
        "data_quality",
        "data_load",
        "table_maintenance",
    )
)


def _as_set(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return frozenset((value,))
    return frozenset(value)


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.allowed_schemas = _as_set(
            self.config.get("allowed_schemas"), DEFAULT_ALLOWED_SCHEMAS
        )
        self.approval_tools = _as_set(
            self.config.get("approval_tools"), DEFAULT_APPROVAL_TOOLS
        )

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", UNKNOWN) or UNKNOWN
        allowed_categories = self.config.get("allowed_categories")
        if allowed_categories is not None:
            allowed_categories = _as_set(allowed_categories, frozenset())
            return "allow" if category in allowed_categories else "refuse"
        if category == UNKNOWN or category not in KNOWN_CATEGORIES:
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        if not hasattr(args, "get"):
            return "deny"

        if tool in TABLE_TOOLS:
            return self._check_table_call(tool, args)
        if tool == "api.fetch":
            return self._check_url(args.get("url"), self.config.get("api_domains"))
        if tool == "webhook.post":
            return self._check_url(args.get("url"), self.config.get("webhook_url"))
        return "deny"

    def _check_table_call(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return "deny"

        table = table.strip()
        if tool in READ_TOOLS:
            return "allow"

        schema = table.split(".", 1)[0] if "." in table else WORK_SCHEMA
        if schema not in self.allowed_schemas:
            return "deny"
        if tool in self.approval_tools:
            return "require_approval"
        return "allow"

    def _check_url(self, url, allowed):
        if not isinstance(url, str) or not url.strip():
            return "deny"
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "deny"
        if allowed is None:
            return "allow"

        host = parsed.hostname
        if host is None:
            return "deny"
        allowed_values = (allowed,) if isinstance(allowed, str) else allowed
        for value in allowed_values:
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value:
                continue
            if value == url:
                return "allow"
            allowed_host = urlparse(value).hostname if "://" in value else value
            if allowed_host and allowed_host.lower() == host.lower():
                return "allow"
        return "deny"

    def end_task(self, task_id):
        pass
