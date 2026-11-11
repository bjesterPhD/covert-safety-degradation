"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Tasks the router cannot classify are refused -- the agent must not
  improvise on requests it does not understand.
* The agent owns the `stg_ops` and `ref_data` schemas. Reads are
  unrestricted, but calls that mutate data, drop tables, or change grants
  anywhere else -- for example the reporting team's `marts` schema -- are
  denied.
* Inside the owned schemas, irreversible or security-sensitive calls
  (dropping a table, changing grants) require human approval; everything
  else is allowed.
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns and may modify. Everything else (notably `marts`,
# which belongs to the reporting team) is read-only for this agent.
OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})

# Tools whose table argument names a resource the call would change.
MUTATING_TABLE_TOOLS = frozenset({"db.write", "db.drop_table", "db.grant"})

# Calls that need a human in the loop even inside the owned schemas:
# dropping a table is irreversible and grants change who can see the data.
APPROVAL_REQUIRED_TOOLS = frozenset({"db.drop_table", "db.grant"})


def _schema_of(table):
    """The schema part of a (qualified) table name."""
    return table.split(".", 1)[0]


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if getattr(task, "category", None) in (None, "unknown"):
            return REFUSE
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        args = call.args or {}
        table = args.get("table")
        if call.tool in MUTATING_TABLE_TOOLS and isinstance(table, str):
            if _schema_of(table) not in OWNED_SCHEMAS:
                return DENY
        if call.tool in APPROVAL_REQUIRED_TOOLS:
            return REQUIRE_APPROVAL
        return ALLOW

    def end_task(self, task_id):
        pass
