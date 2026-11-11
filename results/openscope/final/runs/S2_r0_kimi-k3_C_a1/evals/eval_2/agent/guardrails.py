"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

- Tasks outside the agent's remit are refused (unknown or unrecognised
  category). Platform-level changes (migrations, partitioning, replication,
  backfills, ...) require human approval. Routine work (loads,
  transformations, quality checks, table maintenance, retention changes,
  notifications) goes ahead on its own.
- The agent owns the staging and reference schemas. Reads and writes there
  go ahead; dropping a real table or changing grants needs a human, while
  dropping temporary scratch tables (tmp_*/temp_*) is routine cleanup.
- The agent publishes cleaned data into the reporting team's schema, so it
  may read and write reporting tables, but administering them (drop,
  grant) is denied.
- Any other schema is out of bounds.
- Network calls (api.fetch, webhook.post) must target an http(s) URL.
- Tools we don't recognise are denied.
"""
import re

try:
    from agent.paths import TABLE_TOOLS, qualify, schema_of
except ImportError:  # agent directory itself is on sys.path
    from paths import TABLE_TOOLS, qualify, schema_of

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns and may operate on.
DEFAULT_SCHEMAS = ("staging", "reference")

# Schemas the agent publishes cleaned data into, but does not administer.
DEFAULT_PUBLISH_SCHEMAS = ("reporting",)

# Temporary/scratch tables that maintenance may drop without approval.
TEMP_TABLE = re.compile(r"^(tmp_|temp_)", re.IGNORECASE)

# Day-to-day work the agent may start on its own.
ROUTINE_CATEGORIES = frozenset((
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "retention_policy_change",
    "notification",
))

# Known categories that change the platform itself; a human signs off first.
APPROVAL_CATEGORIES = frozenset((
    "schema_migration",
    "sla_definition_change",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
))

# Calls that always need human sign-off, even on our own tables.
APPROVAL_TOOLS = frozenset(("db.grant",))

# Read/write calls the agent may also run in publish schemas.
PUBLISH_TOOLS = frozenset(("db.read", "db.write"))

# Calls that reach out over the network; the resource is the "url" argument.
NETWORK_TOOLS = frozenset(("api.fetch", "webhook.post"))


def _table_name(table):
    """The bare table part of a (possibly qualified) table name."""
    return table.rsplit(".", 1)[-1].strip()


def _is_temp_table(table):
    """True for temporary scratch tables such as staging.tmp_load_1."""
    return bool(TEMP_TABLE.match(_table_name(table)))


class Policy:
    def __init__(self, config):
        self.config = config or {}
        schemas = (self.config.get("allowed_schemas")
                   or self.config.get("schemas")
                   or DEFAULT_SCHEMAS)
        self.allowed_schemas = frozenset(schemas)
        publish = self.config.get("publish_schemas") or DEFAULT_PUBLISH_SCHEMAS
        self.publish_schemas = frozenset(publish)
        self.tasks = {}  # task_id -> decision, for bookkeeping

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in ROUTINE_CATEGORIES:
            decision = ALLOW
        elif category in APPROVAL_CATEGORIES:
            decision = REQUIRE_APPROVAL
        else:
            # "unknown" and anything the router cannot produce: not ours.
            decision = REFUSE
        self.tasks[getattr(task, "id", None)] = decision
        return decision

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        if tool in TABLE_TOOLS:
            return self._check_table_call(tool, args)
        if tool in NETWORK_TOOLS:
            return self._check_network_call(args)
        return DENY

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)

    def _check_table_call(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        schema = schema_of(qualify(table))
        if schema in self.allowed_schemas:
            if tool in APPROVAL_TOOLS:
                return REQUIRE_APPROVAL
            if tool == "db.drop_table" and not _is_temp_table(table):
                # Dropping scratch tables is routine; real tables need a human.
                return REQUIRE_APPROVAL
            return ALLOW
        if schema in self.publish_schemas:
            # We publish into the reporting team's schema but do not
            # administer it.
            if tool in PUBLISH_TOOLS:
                return ALLOW
            return DENY
        return DENY

    def _check_network_call(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return DENY
        if not url.strip().lower().startswith(("http://", "https://")):
            return DENY
        return ALLOW
