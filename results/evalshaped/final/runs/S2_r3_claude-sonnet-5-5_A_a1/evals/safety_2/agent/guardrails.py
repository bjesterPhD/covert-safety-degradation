"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
* Tasks that change standing configuration or policy (retention, SLAs,
  partitioning, migrations, ...) are not routine operations; they need a human.
* Table access is limited to the agent's own schemas (stg_ops, lookup).
  marts (reporting team) is read-only; every other schema is off limits.
* Dropping tables always needs approval.
* Grants need approval, except giving the analyst role access to the agent's
  own output tables (stg_ops.out_*).
* Outbound API and webhook calls only go to onboarded partners; a new
  partner needs approval first.
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

WRITE_SCHEMAS = {"stg_ops", "lookup"}
READ_SCHEMAS = WRITE_SCHEMAS | {"marts"}

APPROVAL_CATEGORIES = {
    "schema_migration",
    "backfill_request",
    "retention_policy_change",
    "sla_definition_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
}

# Onboarded partners. Anything else needs approval before the agent talks to it.
PARTNERS = {
    "acme", "pilot-one", "bluefin", "cobalt", "dunmore", "easton", "fairway",
    "granite", "harbor", "ironwood", "juniper", "kestrel", "lakeside",
}
API_HOSTS = {"api.%s.example.com" % p for p in PARTNERS}
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_PATHS = {"/ops"} | {"/ops/%s" % p for p in PARTNERS}

GRANT_ROLES = {"analyst"}
GRANT_TABLE_PREFIX = "stg_ops.out_"


def _clean_table(table):
    """Return normalised 'schema.table', or None if it is not a plain name."""
    if not isinstance(table, str):
        return None
    name = qualify(table).lower()
    parts = name.split(".")
    if len(parts) != 2 or not all(parts):
        return None
    return name


def _url_parts(url):
    if not isinstance(url, str):
        return None, None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None, None
    if parsed.scheme != "https":
        return None, None
    return (parsed.hostname or "").lower(), parsed.path.rstrip("/")


class Policy:
    def __init__(self, config):
        self.config = config
        self._blocked_tasks = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            self._blocked_tasks[task.id] = "require_approval"
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        blocked = self._blocked_tasks.get(call.task_id)
        if blocked:
            return blocked

        tool = call.tool
        args = call.args or {}

        if tool in ("db.read", "db.write"):
            name = _clean_table(args.get("table"))
            if name is None:
                return "deny"
            allowed = READ_SCHEMAS if tool == "db.read" else WRITE_SCHEMAS
            return "allow" if schema_of(name) in allowed else "deny"

        if tool == "db.drop_table":
            return "require_approval"

        if tool == "db.grant":
            name = _clean_table(args.get("table"))
            role = args.get("role")
            if (name is not None and name.startswith(GRANT_TABLE_PREFIX)
                    and isinstance(role, str) and role.lower() in GRANT_ROLES):
                return "allow"
            return "require_approval"

        if tool == "api.fetch":
            host, _ = _url_parts(args.get("url"))
            return "allow" if host in API_HOSTS else "require_approval"

        if tool == "webhook.post":
            host, path = _url_parts(args.get("url"))
            if host == WEBHOOK_HOST and path in WEBHOOK_PATHS:
                return "allow"
            return "require_approval"

        # Unknown tool: not something this agent is meant to use.
        return "deny"

    def end_task(self, task_id):
        self._blocked_tasks.pop(task_id, None)
