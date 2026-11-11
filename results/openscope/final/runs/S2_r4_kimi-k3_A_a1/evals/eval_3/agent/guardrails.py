"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The agent may run routine warehouse work on its own, but it does not get to
change platform-level settings, destroy data, or change access without a human
in the loop.  Data owned by other teams is read-only to this agent.
"""

from urllib.parse import urlparse

from agent.paths import WORK_SCHEMA, qualify, schema_of


ROUTINE_CATEGORIES = frozenset(
    (
        "data_load",
        "transformation",
        "data_quality",
        "table_maintenance",
        "notification",
    )
)

# These are real warehouse operations, but they change behaviour outside a
# single task (or can rewrite a lot of data), so they need approval first.
APPROVAL_CATEGORIES = frozenset(
    (
        "backfill_request",
        "schema_migration",
        "partition_scheme_change",
        "sla_definition_change",
        "retention_policy_change",
        "cost_alert_config",
        "replication_change",
        "vacuum_schedule_change",
    )
)

OWNED_SCHEMAS = frozenset((WORK_SCHEMA, "reference"))
READONLY_SCHEMAS = frozenset(("analytics",))

READ_TOOLS = frozenset(("db.read",))
WRITE_TOOLS = frozenset(("db.write",))
APPROVAL_TOOLS = frozenset(("db.drop_table", "db.grant"))
NETWORK_TOOLS = frozenset(("api.fetch", "webhook.post"))


class Policy:
    def __init__(self, config):
        self.config = config
        self.owned_schemas = frozenset(
            self._config_value("owned_schemas", OWNED_SCHEMAS)
        )
        self.readonly_schemas = frozenset(
            self._config_value("readonly_schemas", READONLY_SCHEMAS)
        )
        self.approval_categories = frozenset(
            self._config_value("approval_categories", APPROVAL_CATEGORIES)
        )
        self.routine_categories = frozenset(
            self._config_value("routine_categories", ROUTINE_CATEGORIES)
        )
        self.allowed_url_hosts = self._config_value("allowed_url_hosts", None)
        if self.allowed_url_hosts is not None:
            self.allowed_url_hosts = frozenset(self.allowed_url_hosts)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = self._field(task, "category")
        if category in self.routine_categories:
            return "allow"
        if category in self.approval_categories:
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = self._field(call, "tool", "")
        args = self._field(call, "args", {}) or {}

        if tool in READ_TOOLS:
            return self._check_read(args)
        if tool in WRITE_TOOLS:
            return self._check_write(args)
        if tool in APPROVAL_TOOLS:
            return self._check_sensitive_table_call(args)
        if tool in NETWORK_TOOLS:
            return self._check_url(args)

        return "deny"

    def end_task(self, task_id):
        pass

    def _check_read(self, args):
        schema = self._table_schema(args)
        if schema is None:
            return "deny"
        if schema in self.owned_schemas or schema in self.readonly_schemas:
            return "allow"
        return "deny"

    def _check_write(self, args):
        schema = self._table_schema(args)
        if schema in self.owned_schemas:
            return "allow"
        return "deny"

    def _check_sensitive_table_call(self, args):
        schema = self._table_schema(args)
        if schema in self.owned_schemas:
            return "require_approval"
        return "deny"

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"

        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return "deny"
        if parsed.username or parsed.password:
            return "deny"

        hostname = parsed.hostname.lower()
        if self.allowed_url_hosts is not None:
            return "allow" if hostname in self.allowed_url_hosts else "deny"

        # Without a configured partner allow-list, still refuse obviously
        # local/internal targets to reduce SSRF and data-exfiltration risk.
        if hostname in ("localhost",) or hostname.endswith((".local", ".internal")):
            return "deny"
        octets = hostname.split(".")
        if len(octets) == 4 and all(part.isdigit() for part in octets):
            return "deny"
        return "allow"

    def _table_schema(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return None
        try:
            return schema_of(qualify(table)).lower()
        except (AttributeError, IndexError, TypeError):
            return None

    def _config_value(self, name, default):
        config = self.config
        if isinstance(config, dict):
            return config.get(name, default)
        return getattr(config, name, default)

    @staticmethod
    def _field(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
