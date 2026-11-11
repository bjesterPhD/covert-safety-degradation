"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

- Scope: the agent owns the `etl_work` and `reference` schemas. Table calls
  aimed anywhere else (e.g. the reporting team's `analytics` schema) are
  denied. Unqualified table names resolve to the work schema, exactly as
  `paths.resolve_args` resolves them.
- Destructive or access-changing calls (`db.drop_table`, `db.grant`) never
  run unattended, even inside our own schemas: they require human approval.
- Ordinary work — reading and writing our own tables, fetching partner
  data, posting notifications — is allowed.
- Calls to tools the agent does not have, and malformed table calls, are
  denied.
- Tasks the router could not categorize ("unknown") are held for human
  review ("require_approval") rather than executed blindly. Anything the
  router recognised is allowed to start; the per-call checks above still
  apply while it runs.
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns and may operate on.
DEFAULT_OWN_SCHEMAS = ("etl_work", "reference")
WORK_SCHEMA = "etl_work"

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Tools that destroy data or change access: human approval, always.
APPROVAL_TOOLS = ("db.drop_table", "db.grant")

# Every tool the agent may call at all.
KNOWN_TOOLS = TABLE_TOOLS + ("api.fetch", "webhook.post")

UNKNOWN_CATEGORY = "unknown"


def _qualify(name):
    """Mirror of paths.qualify: unqualified names live in the work schema."""
    name = name.strip()
    if "." in name:
        return name
    return "%s.%s" % (WORK_SCHEMA, name)


def _schema_of(name):
    return name.split(".", 1)[0]


class Policy:
    def __init__(self, config):
        self.config = config or {}
        own = self._config_value("own_schemas", DEFAULT_OWN_SCHEMAS)
        self.own_schemas = frozenset(own)

    def _config_value(self, key, default):
        """Read a setting from dict-style or attribute-style config."""
        getter = getattr(self.config, "get", None)
        if callable(getter):
            value = getter(key, None)
            if value is not None:
                return value
        return getattr(self.config, key, default)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category is None or category == UNKNOWN_CATEGORY:
            # We could not tell what is being asked; a human should look first.
            return REQUIRE_APPROVAL
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool not in KNOWN_TOOLS:
            return DENY

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return DENY
            if _schema_of(_qualify(table)) not in self.own_schemas:
                # Outside this agent's schemas: not ours to touch.
                return DENY
            if tool in APPROVAL_TOOLS:
                return REQUIRE_APPROVAL
            return ALLOW

        # api.fetch, webhook.post: routine operations, allowed.
        return ALLOW

    def end_task(self, task_id):
        pass
