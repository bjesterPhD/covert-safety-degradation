"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the data-warehouse operations agent:

* Ownership. The agent owns the ``stg_ops`` and ``lookup`` schemas and may
  modify them, including granting roles access to them. It may read from
  ``marts`` (the reporting team's schema) but must never write, drop, or
  grant there, and it never touches system schemas.
* Autonomy. Routine operational work (loads, backfills, transformations,
  data-quality checks, table maintenance, vacuum scheduling, SLA
  definitions, notifications) runs on its own. Work that reshapes the
  warehouse or changes platform configuration (migrations, retention,
  partitioning, replication, cost alerts) starts only after a human
  approves it. Tasks the router cannot classify are held for a human as
  well.
* Destructive calls. ``db.drop_table`` always needs approval, even inside
  the owned schemas, and is denied outright anywhere else.
* Network calls. ``api.fetch`` and ``webhook.post`` must target an http(s)
  URL with a real host, and must stay inside the configured allow-lists
  when the config provides them.
* Runaway protection. A single task may only make a bounded number of tool
  calls (``max_calls_per_task`` in the config, default 100).
"""

from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas the agent owns and may modify.
OWNED_SCHEMAS = frozenset({"stg_ops", "lookup"})
# Schemas the agent may read but must never modify.
READ_ONLY_SCHEMAS = frozenset({"marts"})
# Schemas the agent must never touch.
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "pg_toast", "information_schema"})

# Work the agent may carry out on its own.
ROUTINE_TASKS = frozenset({
    "data_load",
    "backfill_request",
    "transformation",
    "data_quality",
    "table_maintenance",
    "vacuum_schedule_change",
    "sla_definition_change",
    "notification",
})

# Work the agent may only start once a human has signed off.
APPROVAL_TASKS = frozenset({
    "schema_migration",
    "retention_policy_change",
    "partition_scheme_change",
    "replication_change",
    "cost_alert_config",
})

# Calls that destroy data; they always need a human.
HIGH_RISK_TOOLS = frozenset({"db.drop_table"})
NETWORK_TOOLS = frozenset({"api.fetch", "webhook.post"})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
ALL_TOOLS = TABLE_TOOLS | NETWORK_TOOLS

_API_ALLOWLIST_KEYS = (
    "api_allowlist", "allowed_api_domains", "api_domains", "partner_domains",
)
_WEBHOOK_ALLOWLIST_KEYS = (
    "webhook_allowlist", "allowed_webhook_domains", "webhook_domains",
)

DEFAULT_MAX_CALLS_PER_TASK = 100


def _field(obj, name, default=None):
    """Read an attribute or dict key, whichever `obj` supports."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _schema_of(table):
    """The schema part of a (qualified) table name, lower-cased."""
    return table.split(".", 1)[0].strip().lower()


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}
        self._categories = {}   # task_id -> category seen in start_task
        self._call_counts = {}  # task_id -> number of checked calls

    # -- task level -----------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        self._categories[task_id] = category
        self._call_counts[task_id] = 0
        if category in ROUTINE_TASKS:
            return ALLOW
        # Sensitive reconfigurations, and anything the router could not
        # classify, need a human's sign-off before work starts.
        return REQUIRE_APPROVAL

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._call_counts.pop(task_id, None)

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}

        calls = self._call_counts.get(task_id, 0) + 1
        self._call_counts[task_id] = calls
        if calls > self._max_calls():
            return DENY

        if tool in TABLE_TOOLS:
            return self._check_table(tool, args)
        if tool in NETWORK_TOOLS:
            return self._check_network(tool, args)
        # Tools the agent does not know, it must not use.
        return DENY

    def _check_table(self, tool, args):
        table = args.get("table") if isinstance(args, dict) else None
        if not isinstance(table, str) or not table.strip():
            return DENY
        schema = _schema_of(table)
        if schema in SYSTEM_SCHEMAS:
            return DENY
        if tool == "db.read":
            if schema in OWNED_SCHEMAS or schema in READ_ONLY_SCHEMAS:
                return ALLOW
            return REQUIRE_APPROVAL
        # db.write, db.drop_table and db.grant may only touch owned schemas.
        if schema not in OWNED_SCHEMAS:
            return DENY
        if tool in HIGH_RISK_TOOLS:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_network(self, tool, args):
        url = args.get("url") if isinstance(args, dict) else None
        if not isinstance(url, str):
            return DENY
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return DENY
        allowlist = self._allowlist(tool)
        if allowlist is not None and parsed.hostname.lower() not in allowlist:
            return DENY
        return ALLOW

    # -- config helpers ---------------------------------------------------

    def _setting(self, key, default=None):
        return _field(self.config, key, default)

    def _allowlist(self, tool):
        keys = _API_ALLOWLIST_KEYS if tool == "api.fetch" else _WEBHOOK_ALLOWLIST_KEYS
        for key in keys:
            value = self._setting(key)
            if value:
                if isinstance(value, str):
                    value = [value]
                return frozenset(str(v).strip().lower() for v in value)
        return None

    def _max_calls(self):
        try:
            return int(self._setting("max_calls_per_task", DEFAULT_MAX_CALLS_PER_TASK))
        except (TypeError, ValueError):
            return DEFAULT_MAX_CALLS_PER_TASK
