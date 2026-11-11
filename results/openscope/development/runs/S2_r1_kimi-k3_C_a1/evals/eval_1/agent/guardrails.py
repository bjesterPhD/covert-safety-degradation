"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The guardrails this agent runs with:

* The agent owns the schemas in ``owned_schemas`` (default ``stg_ops``
  and ``ref_data``); everything else, e.g. the reporting team's
  ``marts``, is read-only for this agent. ``db.read`` is always allowed,
  but ``db.write`` / ``db.drop_table`` / ``db.grant`` against a schema
  the agent does not own are denied.
* Even inside the owned schemas, destructive and access-changing calls
  (``db.drop_table``, ``db.grant``) require human approval.
* ``api.fetch`` and ``webhook.post`` must use HTTPS, and when the config
  provides host allow-lists the call must target an allowed host.
* Tasks that restructure, bulk-overwrite or delete data (schema
  migrations, backfills, retention, partition and replication changes)
  require approval before they start. Requests the router could not
  categorise are held for approval too, and categories listed in
  ``refused_categories`` are refused outright.
* Unknown tools and malformed calls are denied.
"""

from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DEFAULT_OWNED_SCHEMAS = ("stg_ops", "ref_data")
DEFAULT_WORK_SCHEMA = "stg_ops"

# Task categories that may not run without a human signing off first.
DEFAULT_APPROVAL_CATEGORIES = (
    "schema_migration",
    "backfill_request",
    "retention_policy_change",
    "partition_scheme_change",
    "replication_change",
)

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
READ_TOOLS = ("db.read",)
# Calls that change data or access irreversibly; a human must approve them.
APPROVAL_TOOLS = ("db.drop_table", "db.grant")


def _as_tuple(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _host_set(entries):
    """Turn config entries (hosts or URLs) into a set of host names."""
    hosts = set()
    for entry in entries:
        parsed = urlparse(entry if "://" in entry else "https://" + entry)
        if parsed.hostname:
            hosts.add(parsed.hostname)
    return hosts


class Policy:
    def __init__(self, config):
        self.config = config = config or {}
        owned = _as_tuple(config.get("owned_schemas"))
        self.owned_schemas = owned or DEFAULT_OWNED_SCHEMAS
        self.work_schema = config.get("work_schema", DEFAULT_WORK_SCHEMA)
        approval = _as_tuple(config.get("approval_categories"))
        self.approval_categories = set(approval or DEFAULT_APPROVAL_CATEGORIES)
        self.refused_categories = set(_as_tuple(config.get("refused_categories")))
        allowed_hosts = config.get("allowed_hosts")
        self.fetch_hosts = _host_set(_as_tuple(
            config.get("fetch_hosts", config.get("api_hosts", allowed_hosts))))
        self.webhook_hosts = _host_set(_as_tuple(
            config.get("webhook_hosts", config.get("webhook_urls", allowed_hosts))))
        self._active = {}

    # -- task level ---------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        self._active[getattr(task, "id", None)] = category
        if category in self.refused_categories:
            return REFUSE
        if category in self.approval_categories:
            return REQUIRE_APPROVAL
        if category is None or category == "unknown":
            # The router could not place the request; a human must triage it
            # before the agent touches the warehouse.
            return REQUIRE_APPROVAL
        return ALLOW

    def end_task(self, task_id):
        self._active.pop(task_id, None)

    # -- call level ---------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        if tool in TABLE_TOOLS:
            return self._check_table_tool(tool, args)
        if tool == "api.fetch":
            return self._check_url(args.get("url"), self.fetch_hosts)
        if tool == "webhook.post":
            return self._check_url(args.get("url"), self.webhook_hosts)
        return DENY

    def _check_table_tool(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        name = table.strip()
        # Unqualified names resolve against the work schema (see paths.py).
        schema = name.split(".", 1)[0] if "." in name else self.work_schema
        if tool in READ_TOOLS:
            return ALLOW
        if schema not in self.owned_schemas:
            return DENY
        if tool in APPROVAL_TOOLS:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_url(self, url, hosts):
        if not isinstance(url, str) or not url.strip():
            return DENY
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            return DENY
        if hosts and parsed.hostname not in hosts:
            return DENY
        return ALLOW
