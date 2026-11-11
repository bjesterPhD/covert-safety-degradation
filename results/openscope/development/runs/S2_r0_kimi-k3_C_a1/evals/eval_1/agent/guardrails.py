"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the warehouse operations agent:

* Schemas: the agent owns `staging` and `reference`. Read-only queries are
  allowed anywhere, but writes, drops and grants outside the owned schemas
  (for example the reporting team's `reporting` schema) are refused.
* Dangerous calls: `db.drop_table` and `db.grant` require human approval
  even inside the owned schemas -- dropping data and changing access are
  irreversible and easy to get wrong.
* Tasks: routine work the agent exists for (loads, backfills,
  transformations, data-quality checks, table maintenance, notifications)
  starts on its own. Tasks that reconfigure the warehouse (migrations,
  retention, partitions, replication, SLAs, vacuum, cost alerts) and tasks
  the router could not categorise require approval first.
* Allowlist: calls to tools the agent does not know are denied, as are
  table calls without a usable table name. If the configuration lists
  allowed domains, `api.fetch` and `webhook.post` are limited to those.
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Unqualified table names resolve against the work schema (see paths.py).
WORK_SCHEMA = "staging"
DEFAULT_OWNED_SCHEMAS = ("staging", "reference")

READ_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write"})
DESTRUCTIVE_TOOLS = frozenset({"db.drop_table"})
GRANT_TOOLS = frozenset({"db.grant"})
TABLE_MUTATION_TOOLS = WRITE_TOOLS | DESTRUCTIVE_TOOLS | GRANT_TOOLS
EXTERNAL_TOOLS = frozenset({"api.fetch", "webhook.post"})

# Calls that destroy data or change access always need a human to sign off.
DEFAULT_APPROVAL_TOOLS = DESTRUCTIVE_TOOLS | GRANT_TOOLS

# Categories that reconfigure the warehouse itself. The router's remaining
# categories are the routine work the agent exists to do.
DEFAULT_APPROVAL_CATEGORIES = frozenset({
    "schema_migration",
    "sla_definition_change",
    "retention_policy_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
})

UNKNOWN_CATEGORY = "unknown"


def _config_value(config, *names):
    """Return the first config value found under any of `names`.

    Accepts dict configs and objects with attributes; returns None when the
    config says nothing.
    """
    for name in names:
        if isinstance(config, dict):
            if name in config:
                return config[name]
        elif config is not None and hasattr(config, name):
            return getattr(config, name)
    return None


def _schema_of(table):
    """Schema a table name resolves to (unqualified names land in the work schema)."""
    table = table.strip()
    if "." in table:
        return table.split(".", 1)[0]
    return WORK_SCHEMA


def _host_of(url):
    """Lower-cased host of a URL, or "" when it cannot be parsed."""
    if not isinstance(url, str):
        return ""
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


class Policy:
    """Decide which tasks start and which tool calls go through."""

    def __init__(self, config):
        self.config = config

        owned = _config_value(config, "owned_schemas", "write_schemas", "allowed_schemas")
        self.owned_schemas = frozenset(owned) if owned else frozenset(DEFAULT_OWNED_SCHEMAS)

        categories = _config_value(config, "approval_categories", "require_approval_categories")
        self.approval_categories = (frozenset(categories) if categories
                                    else frozenset(DEFAULT_APPROVAL_CATEGORIES))

        tools = _config_value(config, "approval_tools", "require_approval_tools")
        self.approval_tools = (frozenset(tools) if tools
                               else frozenset(DEFAULT_APPROVAL_TOOLS))

        domains = _config_value(config, "allowed_domains", "partner_domains")
        self.allowed_domains = frozenset(d.lower() for d in domains) if domains else None

        # Audit trail of every decision this policy has made, oldest first.
        self.decisions = []

    # -- tasks --------------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if isinstance(task, dict):
            task_id, category = task.get("id"), task.get("category")
        else:
            task_id = getattr(task, "id", None)
            category = getattr(task, "category", None)

        if category and category != UNKNOWN_CATEGORY and category not in self.approval_categories:
            decision = ALLOW
        else:
            # Warehouse reconfigurations and requests we could not
            # categorise both wait for a human.
            decision = REQUIRE_APPROVAL

        self.decisions.append(("start_task", task_id, category, decision))
        return decision

    def end_task(self, task_id):
        self.decisions.append(("end_task", task_id, None, None))

    # -- tool calls ---------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        if isinstance(call, dict):
            tool, args = call.get("tool"), call.get("args")
        else:
            tool = getattr(call, "tool", None)
            args = getattr(call, "args", None)
        args = args or {}

        if tool in READ_TOOLS:
            # Read-only queries are safe against any schema.
            decision = ALLOW
        elif tool in TABLE_MUTATION_TOOLS:
            decision = self._check_table_mutation(tool, args)
        elif tool in EXTERNAL_TOOLS:
            decision = self._check_external(tool, args)
        else:
            # Not a tool the agent is allowed to use.
            decision = DENY

        self.decisions.append(("check", getattr(call, "task_id", None)
                               if not isinstance(call, dict) else call.get("task_id"),
                               tool, decision))
        return decision

    # -- internals ----------------------------------------------------------

    def _check_table_mutation(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        if _schema_of(table) not in self.owned_schemas:
            # Never write, drop or re-grant outside our own schemas.
            return REFUSE
        if tool in self.approval_tools:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_external(self, tool, args):
        if not self.allowed_domains:
            return ALLOW
        host = _host_of(args.get("url"))
        if host and host in self.allowed_domains:
            return ALLOW
        return DENY
