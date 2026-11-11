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
checking its quality, maintaining tables, and posting notifications. Tasks in
those categories run autonomously. Anything else the router recognises
(schema migrations, retention or replication changes, backfills, ...) and
anything it does not recognise waits for a human to approve it first.

Call level: the agent owns the `stg_ops` and `ref_data` schemas. Reads are
unrestricted, but nothing outside those two schemas may be written, dropped,
or re-granted -- `marts` belongs to the reporting team. Even inside its own
schemas, destructive or access-changing calls (`db.drop_table`, `db.grant`)
require approval. Network calls (`api.fetch`, `webhook.post`) are part of the
agent's normal job and are allowed. Unknown tools fail closed.
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
})

READ_ONLY_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write"})
APPROVAL_TOOLS = frozenset({"db.drop_table", "db.grant"})
NETWORK_TOOLS = frozenset({"api.fetch", "webhook.post"})
KNOWN_TOOLS = READ_ONLY_TOOLS | WRITE_TOOLS | APPROVAL_TOOLS | NETWORK_TOOLS


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

        if tool in WRITE_TOOLS or tool in APPROVAL_TOOLS:
            schema = self._table_schema(args.get("table"))
            if schema is None or schema not in self.owned_schemas:
                # Malformed call, or a table outside this agent's schemas.
                return DENY
            if tool in APPROVAL_TOOLS:
                # Destructive / access-changing even in our own schemas.
                return REQUIRE_APPROVAL
            return ALLOW

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
