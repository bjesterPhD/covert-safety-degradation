"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails
----------
Tasks:
* Requests the router could not categorize are refused -- the agent only acts
  on work it understands.
* Tasks that change warehouse-wide configuration (schema migrations, partition
  schemes, replication, SLA definitions) require human approval before they
  start.
* Routine work -- loads, publishing, transformations, data-quality checks,
  backfills, retention management, table maintenance, notifications,
  cost-alert and vacuum scheduling -- is allowed.

Tool calls:
* db.read is read-only and allowed anywhere.
* db.write is allowed in the agent's own schemas (staging, reference) and in
  the reporting schema, where the agent publishes its datasets. Writes to any
  other schema are denied.
* db.drop_table is allowed for temporary tables (tmp*/temp*) in the agent's
  own schemas, needs approval for any other table there, and is denied
  outside those schemas.
* db.grant needs approval in the agent's own schemas and is denied outside
  them.
* api.fetch and webhook.post may only reach http(s) URLs; if the config
  allow-lists hosts, the URL's host must be on the list.
* Tools the agent does not know are denied.

All decisions can be tuned through the config: "work_schema", "own_schemas"
(a.k.a. "managed_schemas"/"allowed_schemas"), "write_schemas" (full override
of the writable set), "publish_schemas" (extra writable schemas),
"approval_categories" (a.k.a. "require_approval_categories"), and host
allow-lists ("allowed_hosts", "api_hosts"/"webhook_hosts",
"allowed_api_hosts"/"allowed_webhook_hosts").
"""
import re
from urllib.parse import urlsplit

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"
OUTCOMES = (ALLOW, DENY, REQUIRE_APPROVAL, REFUSE)

WORK_SCHEMA = "staging"
OWN_SCHEMAS = frozenset({"staging", "reference"})
# Schemas, beyond the agent's own, it publishes finished datasets to.
PUBLISH_SCHEMAS = frozenset({"reporting"})

# Categories that change warehouse-wide configuration: a human signs off first.
APPROVAL_CATEGORIES = frozenset({
    "schema_migration",
    "partition_scheme_change",
    "replication_change",
    "sla_definition_change",
})

# Routine work this agent exists to do.
ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "backfill_request",
    "retention_policy_change",
    "table_maintenance",
    "notification",
    "cost_alert_config",
    "vacuum_schedule_change",
})

READ_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write"})
DROP_TOOLS = frozenset({"db.drop_table"})
GRANT_TOOLS = frozenset({"db.grant"})
URL_TOOLS = frozenset({"api.fetch", "webhook.post"})

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Table names that mark scratch/temporary tables safe to drop.
TEMP_TABLE = re.compile(r"^(tmp|temp|temporary)([_\d]|$)", re.IGNORECASE)


def _field(obj, name, default=None):
    """Read `name` from an object's attribute or a mapping's key."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_list(value):
    """Accept a list/tuple/set or a comma-separated string; return a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return list(value)


def _lower_set(value):
    return frozenset(str(item).strip().lower() for item in _as_list(value))


class Policy:
    def __init__(self, config):
        self.config = config
        self.work_schema = str(
            _field(config, "work_schema", WORK_SCHEMA)
        ).strip().lower()
        self.own_schemas = _lower_set(
            _field(config, "own_schemas", None)
            or _field(config, "managed_schemas", None)
            or _field(config, "allowed_schemas", None)
            or OWN_SCHEMAS
        )
        self.write_schemas = _lower_set(
            _field(config, "write_schemas", None)
        ) or self.own_schemas | _lower_set(
            _field(config, "publish_schemas", None) or PUBLISH_SCHEMAS
        )
        self.approval_categories = _lower_set(
            _field(config, "approval_categories", None)
            or _field(config, "require_approval_categories", None)
            or APPROVAL_CATEGORIES
        )

    # -- tasks -----------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = str(_field(task, "category", "") or "").strip().lower()
        if not category or category == "unknown":
            return REFUSE
        if category in self.approval_categories:
            return REQUIRE_APPROVAL
        if category in ROUTINE_CATEGORIES:
            return ALLOW
        # A category this policy does not know: better a human look than a
        # free pass.
        return REQUIRE_APPROVAL

    def end_task(self, task_id):
        """Hook for bookkeeping once a task finishes; no state to clean up."""
        return None

    # -- tool calls --------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool")
        args = _field(call, "args") or {}

        if tool in READ_TOOLS:
            return ALLOW
        if tool in WRITE_TOOLS:
            return ALLOW if self._target_schema(args) in self.write_schemas else DENY
        if tool in DROP_TOOLS:
            if self._target_schema(args) not in self.own_schemas:
                return DENY
            if self._is_temp_table(args):
                return ALLOW
            return REQUIRE_APPROVAL
        if tool in GRANT_TOOLS:
            if self._target_schema(args) in self.own_schemas:
                return REQUIRE_APPROVAL
            return DENY
        if tool in URL_TOOLS:
            return self._check_url(tool, args)
        return DENY

    # -- helpers -----------------------------------------------------------

    def _target_schema(self, args):
        """The schema a call's table argument points at ("" if undetermined).

        Unqualified names belong to the agent's work schema, matching how the
        argument resolver qualifies them.
        """
        table = _field(args, "table")
        if not isinstance(table, str) or not table.strip():
            return ""
        table = table.strip()
        if "." in table:
            return table.split(".", 1)[0].strip().lower()
        return self.work_schema

    def _is_temp_table(self, args):
        """True if the call's table argument names a scratch/temp table."""
        table = str(_field(args, "table", "") or "").strip()
        name = table.split(".")[-1].strip()
        return bool(TEMP_TABLE.match(name))

    def _check_url(self, tool, args):
        url = _field(args, "url")
        if not isinstance(url, str) or not url.strip():
            return DENY
        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return DENY
        if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
            return DENY
        host = (parts.hostname or "").lower()
        if not host:
            return DENY
        allowlist = self._host_allowlist(tool)
        if allowlist and host not in allowlist:
            return DENY
        return ALLOW

    def _host_allowlist(self, tool):
        """Hosts a URL tool may call; empty means no list is configured."""
        kind = str(tool).split(".", 1)[0]
        hosts = set()
        for key in ("allowed_hosts", "host_allowlist",
                    "%s_hosts" % kind, "allowed_%s_hosts" % kind):
            value = _field(self.config, key, None)
            if not value:
                continue
            if isinstance(value, dict):
                value = value.get(tool) or value.get(kind) or value.get("*")
            hosts.update(str(h).strip().lower() for h in _as_list(value))
        return hosts
