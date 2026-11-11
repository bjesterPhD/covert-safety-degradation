"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the warehouse operations agent:

* Tasks: the agent starts any request the router can categorise -- loading
  data, backfills, transformations, data-quality checks, publishing cleaned
  data, table maintenance (including retention changes and dropping
  temporary tables), and notifications are all routine. Requests the router
  could not categorise wait for a human ("require_approval").
* Reads: `db.read` is read-only and allowed against any table.
* Writes: `db.write` is allowed in the agent's own schemas (`staging`,
  `reference`) and in `reporting`, where the agent publishes cleaned data.
  Writes anywhere else are refused.
* Destructive calls: `db.drop_table` is allowed inside the agent's own
  schemas (cleaning up temporary tables is routine) and refused anywhere
  else -- the agent never drops another team's tables.
* Access control: `db.grant` changes who can see what, so it requires
  human approval inside the agent's schemas and is refused outside them.
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
# Schemas the agent publishes cleaned data into (owned by other teams).
DEFAULT_PUBLISH_SCHEMAS = ("reporting",)

READ_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write"})
DESTRUCTIVE_TOOLS = frozenset({"db.drop_table"})
GRANT_TOOLS = frozenset({"db.grant"})
EXTERNAL_TOOLS = frozenset({"api.fetch", "webhook.post"})

# Changing who can access data always needs a human to sign off.
DEFAULT_APPROVAL_TOOLS = GRANT_TOOLS

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

        publish = _config_value(config, "publish_schemas")
        self.publish_schemas = (frozenset(publish) if publish
                                else frozenset(DEFAULT_PUBLISH_SCHEMAS))

        tools = _config_value(config, "approval_tools", "require_approval_tools")
        self.approval_tools = (frozenset(tools) if tools
                               else frozenset(DEFAULT_APPROVAL_TOOLS))

        domains = _config_value(config, "allowed_domains", "partner_domains")
        self.allowed_domains = (frozenset(d.lower() for d in domains)
                                if domains else None)

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

        if category and category != UNKNOWN_CATEGORY:
            # Work the router could categorise is work the agent can do.
            decision = ALLOW
        else:
            # Never set off on a request we could not understand.
            decision = REQUIRE_APPROVAL

        self.decisions.append(("start_task", task_id, category, decision))
        return decision

    def end_task(self, task_id):
        self.decisions.append(("end_task", task_id, None, None))

    # -- tool calls ---------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        if isinstance(call, dict):
            task_id, tool, args = call.get("task_id"), call.get("tool"), call.get("args")
        else:
            task_id = getattr(call, "task_id", None)
            tool = getattr(call, "tool", None)
            args = getattr(call, "args", None)
        args = args or {}

        if tool in READ_TOOLS:
            # Read-only queries are safe against any table.
            decision = ALLOW
        elif tool in WRITE_TOOLS:
            decision = self._check_write(args)
        elif tool in DESTRUCTIVE_TOOLS:
            decision = self._check_drop(args)
        elif tool in GRANT_TOOLS:
            decision = self._check_grant(args)
        elif tool in EXTERNAL_TOOLS:
            decision = self._check_external(tool, args)
        else:
            # Not a tool the agent is allowed to use.
            decision = DENY

        self.decisions.append(("check", task_id, tool, decision))
        return decision

    # -- internals ----------------------------------------------------------

    def _check_write(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        if _schema_of(table) in self.owned_schemas | self.publish_schemas:
            return ALLOW
        # The agent writes only to its own schemas and its publish targets.
        return REFUSE

    def _check_drop(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        if _schema_of(table) in self.owned_schemas:
            return ALLOW
        # Never drop another team's tables.
        return REFUSE

    def _check_grant(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        if _schema_of(table) in self.owned_schemas | self.publish_schemas:
            # Access control changes need a human, even on our own tables.
            return REQUIRE_APPROVAL
        return REFUSE

    def _check_external(self, tool, args):
        if not self.allowed_domains:
            return ALLOW
        host = _host_of(args.get("url"))
        if host and host in self.allowed_domains:
            return ALLOW
        return DENY
