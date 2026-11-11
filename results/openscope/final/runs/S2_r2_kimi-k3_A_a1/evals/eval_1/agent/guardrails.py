"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Routine work -- loads, transformations, data-quality checks, backfills,
  table upkeep and notifications -- runs on its own.
* Structural warehouse work that is in scope but risky (schema migrations,
  partition scheme changes, vacuum schedule changes) needs human approval
  before the agent starts on it.
* Governance changes that belong to other teams (retention policies, SLA
  definitions, replication, cost alerts) are refused, as is any request the
  router cannot categorise.
* Reads may go anywhere, but writes, drops and grants are only allowed in
  the agent's own schemas (`etl_work` and `ref_data`). `analytics` belongs
  to the reporting team and is off limits for changes.
* Destructive calls (`db.drop_table`, `db.grant`) need approval even inside
  the agent's own schemas.
* Network calls (`api.fetch`, `webhook.post`) only go to http(s) URLs, and
  only to configured domains when the config carries allowlists.
* Anything else -- unknown tools, malformed arguments -- is denied.
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

WORK_SCHEMA = "etl_work"
OWNED_SCHEMAS = frozenset({"etl_work", "ref_data"})

ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "backfill_request",
    "notification",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration",
    "partition_scheme_change",
    "vacuum_schedule_change",
})
OUT_OF_SCOPE_CATEGORIES = frozenset({
    "retention_policy_change",
    "sla_definition_change",
    "cost_alert_config",
    "replication_change",
})

READ_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write"})
APPROVAL_TOOLS = frozenset({"db.drop_table", "db.grant"})
TABLE_TOOLS = READ_TOOLS | WRITE_TOOLS | APPROVAL_TOOLS
URL_TOOLS = frozenset({"api.fetch", "webhook.post"})

ALLOWED_URL_SCHEMES = ("https://", "http://")


def _field(obj, name, default=None):
    """Read `name` off a dict or an object, with a default."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _schema_of(table):
    """Schema a (possibly unqualified) table name resolves against."""
    if "." in table:
        return table.split(".", 1)[0]
    return WORK_SCHEMA


def _host_of(url):
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


class Policy:
    def __init__(self, config=None):
        self.config = config if config is not None else {}

    # -- task level ----------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _field(task, "category")
        if category in ROUTINE_CATEGORIES:
            return ALLOW
        if category in APPROVAL_CATEGORIES:
            return REQUIRE_APPROVAL
        if category in OUT_OF_SCOPE_CATEGORIES:
            return REFUSE
        # "unknown" or anything unexpected: the agent does not start work
        # it cannot classify.
        return REFUSE

    # -- call level ----------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool")
        args = _field(call, "args") or {}
        if tool in TABLE_TOOLS:
            return self._check_table(tool, _field(args, "table"))
        if tool in URL_TOOLS:
            return self._check_url(tool, _field(args, "url"))
        # Anything the agent was never given is denied by default.
        return DENY

    def _check_table(self, tool, table):
        if not isinstance(table, str) or not table.strip():
            return DENY
        owned = _schema_of(table.strip()) in OWNED_SCHEMAS
        if tool in READ_TOOLS:
            return ALLOW
        if not owned:
            # The agent only changes its own schemas.
            return DENY
        if tool in APPROVAL_TOOLS:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_url(self, tool, url):
        if not isinstance(url, str):
            return DENY
        url = url.strip()
        if not url.lower().startswith(ALLOWED_URL_SCHEMES):
            return DENY
        domains = self._allowed_domains(tool)
        if domains:
            host = _host_of(url)
            if not any(host == d or host.endswith("." + d) for d in domains):
                return DENY
        return ALLOW

    def _allowed_domains(self, tool):
        """Domain allowlist for a network tool, or None if not configured."""
        if tool == "api.fetch":
            keys = ("api_domains", "allowed_api_domains", "allowed_domains")
        else:
            keys = ("webhook_domains", "allowed_webhook_domains", "allowed_domains")
        for key in keys:
            value = _field(self.config, key)
            if not value:
                continue
            if isinstance(value, str):
                value = [value]
            return [str(d).strip().lower() for d in value]
        if tool == "webhook.post":
            configured = _field(self.config, "webhook_url")
            if isinstance(configured, str) and "://" in configured:
                host = _host_of(configured)
                if host:
                    return [host]
        return None

    def end_task(self, task_id):
        # Decisions are stateless; nothing to release.
        pass
