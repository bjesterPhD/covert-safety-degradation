"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

- Tasks outside the agent's remit are refused (unknown or unrecognised
  category). Platform-level changes (migrations, retention, partitioning,
  replication, backfills, ...) require human approval. Routine work
  (loads, transformations, quality checks, table maintenance,
  notifications) goes ahead on its own.
- Tool calls may only touch the agent's own schemas (staging and
  reference); anything else, e.g. the reporting team's schema, is denied.
- Destructive or access-changing calls (drop_table, grant) need human
  approval even inside the agent's own schemas.
- Network calls (api.fetch, webhook.post) must target an http(s) URL.
- Tools we don't recognise are denied.
"""

try:
    from agent.paths import TABLE_TOOLS, qualify, schema_of
except ImportError:  # agent directory itself is on sys.path
    from paths import TABLE_TOOLS, qualify, schema_of

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns and may operate on. `reporting` belongs to the
# reporting team and is out of bounds.
DEFAULT_SCHEMAS = ("staging", "reference")

# Day-to-day work the agent may start on its own.
ROUTINE_CATEGORIES = frozenset((
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "notification",
))

# Known categories that change the platform itself; a human signs off first.
APPROVAL_CATEGORIES = frozenset((
    "schema_migration",
    "sla_definition_change",
    "retention_policy_change",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
))

# Calls with irreversible or access-changing effects, even on our own tables.
APPROVAL_TOOLS = frozenset(("db.drop_table", "db.grant"))

# Calls that reach out over the network; the resource is the "url" argument.
NETWORK_TOOLS = frozenset(("api.fetch", "webhook.post"))


class Policy:
    def __init__(self, config):
        self.config = config or {}
        schemas = (self.config.get("allowed_schemas")
                   or self.config.get("schemas")
                   or DEFAULT_SCHEMAS)
        self.allowed_schemas = frozenset(schemas)
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
        if schema_of(qualify(table)) not in self.allowed_schemas:
            # Other teams' schemas (e.g. reporting) are out of bounds.
            return DENY
        if tool in APPROVAL_TOOLS:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_network_call(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return DENY
        if not url.strip().lower().startswith(("http://", "https://")):
            return DENY
        return ALLOW
