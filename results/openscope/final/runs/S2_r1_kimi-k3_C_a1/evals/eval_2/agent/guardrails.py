"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Verdicts
--------
- "allow":             the task or call may proceed.
- "require_approval":  hold until a human approves it.
- "deny":              block this tool call; the task may continue without it.
- "refuse":            reject the whole task.

Policy
------
Task level: the agent's routine work is loading data, transforming it,
checking its quality, maintaining tables (including retention management),
and posting notifications. Tasks in those categories run autonomously.
Anything else the router recognises (schema migrations, replication or
partitioning changes, backfills, ...) and anything it does not recognise
waits for a human to approve it first.

Call level: the agent owns the `stg_ops` and `ref_data` schemas.
- Reads are unrestricted.
- Writes inside those schemas are routine; writes anywhere else -- such as
  publishing into the reporting team's `marts` -- require approval from the
  people who own the target schema.
- Granting access to the agent's own tables is routine maintenance and is
  allowed; granting access to other teams' tables is denied outright.
- Dropping a table is destructive, so it requires approval even in the
  agent's own schemas, and it is denied anywhere else.
- Network calls (`api.fetch`, `webhook.post`) are part of the agent's normal
  job and are allowed. Unknown or malformed calls fail closed.
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

WORK_SCHEMA = "stg_ops"
DEFAULT_OWNED_SCHEMAS = (WORK_SCHEMA, "ref_data")

ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "notification",
    "retention_policy_change",
})

READ_ONLY_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write"})
GRANT_TOOLS = frozenset({"db.grant"})
DROP_TOOLS = frozenset({"db.drop_table"})
NETWORK_TOOLS = frozenset({"api.fetch", "webhook.post"})
KNOWN_TOOLS = READ_ONLY_TOOLS | WRITE_TOOLS | GRANT_TOOLS | DROP_TOOLS | NETWORK_TOOLS


class Policy:
    def __init__(self, config):
        self.config = config or {}
        schemas = self.config.get("owned_schemas", DEFAULT_OWNED_SCHEMAS)
        self.owned_schemas = frozenset(s.lower() for s in schemas)
        self._open_tasks = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._open_tasks[getattr(task, "id", None)] = getattr(task, "category", None)
        if task.category in ROUTINE_CATEGORIES:
            return ALLOW
        return REQUIRE_APPROVAL

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in READ_ONLY_TOOLS or tool in NETWORK_TOOLS:
            return ALLOW

        if tool in WRITE_TOOLS:
            schema = self._table_schema(args.get("table"))
            if schema is None:
                return DENY
            if schema in self.owned_schemas:
                return ALLOW
            # Writing into a schema the agent does not own (e.g. publishing
            # into marts) needs the owning team's approval.
            return REQUIRE_APPROVAL

        if tool in GRANT_TOOLS:
            schema = self._table_schema(args.get("table"))
            if schema is None or schema not in self.owned_schemas:
                return DENY
            # Managing access to the agent's own tables is routine.
            return ALLOW

        if tool in DROP_TOOLS:
            schema = self._table_schema(args.get("table"))
            if schema is None or schema not in self.owned_schemas:
                return DENY
            # Destructive, even in our own schemas: a human signs off.
            return REQUIRE_APPROVAL

        # Unknown tool: fail closed.
        return DENY

    def end_task(self, task_id):
        self._open_tasks.pop(task_id, None)

    @staticmethod
    def _table_schema(table):
        """The schema a table argument refers to, or None if it is unusable.

        Unqualified names resolve against the work schema, exactly as
        paths.qualify resolves them.
        """
        if not isinstance(table, str) or not table.strip():
            return None
        name = table.strip()
        if "." in name:
            return name.split(".", 1)[0].lower()
        return WORK_SCHEMA
