"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

- Scope: the agent owns the `etl_work` and `reference` schemas and works
  freely in them. The reporting team's `analytics` schema is not ours to
  manage: the agent may publish curated output there (`db.write`) but may
  not read, drop, or re-grant its tables, and no other out-of-scope schema
  may be touched at all. Unqualified table names resolve to the work
  schema, exactly as `paths.resolve_args` resolves them.
- Dropping tables: routine cleanup of temporary tables (`tmp_*`) inside
  our own schemas is allowed outright. Dropping any other table is
  destructive and always requires human approval first.
- Access changes (`db.grant`) never run unattended: they require human
  approval, and only inside our own schemas.
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

# Schemas the agent may publish curated output to (db.write only). It does
# not manage these schemas: no reads, drops, or grants there.
DEFAULT_PUBLISH_SCHEMAS = ("analytics",)

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Tools that change who can see data: human approval, always.
APPROVAL_TOOLS = ("db.grant",)

# Prefix marking temporary tables; dropping these is routine cleanup.
TEMP_TABLE_PREFIX = "tmp_"

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


def _table_of(name):
    return name.split(".", 1)[1]


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.own_schemas = frozenset(
            self._config_value("own_schemas", DEFAULT_OWN_SCHEMAS)
        )
        self.publish_schemas = frozenset(
            self._config_value("publish_schemas", DEFAULT_PUBLISH_SCHEMAS)
        )

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

        if tool not in TABLE_TOOLS:
            # api.fetch, webhook.post: routine operations, allowed.
            return ALLOW

        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        qualified = _qualify(table)
        schema = _schema_of(qualified)

        if schema in self.own_schemas:
            if tool == "db.drop_table":
                if _table_of(qualified).lower().startswith(TEMP_TABLE_PREFIX):
                    # Routine cleanup of temporary tables.
                    return ALLOW
                # Dropping a real table is destructive: approve first.
                return REQUIRE_APPROVAL
            if tool in APPROVAL_TOOLS:
                return REQUIRE_APPROVAL
            return ALLOW

        if tool == "db.write" and schema in self.publish_schemas:
            # Publishing curated output is part of the job.
            return ALLOW

        # Outside this agent's scope: not ours to touch.
        return DENY

    def end_task(self, task_id):
        pass
