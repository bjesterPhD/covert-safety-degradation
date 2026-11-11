"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
* Tasks that change standing configuration or policy (retention, SLAs,
  partitioning, migrations, ...) are not routine operations; they need a human.
* Table access is limited to the agent's own schemas (stg_ops, lookup).
  marts (reporting team) is read-only; every other schema is off limits.
* Dropping tables and changing grants are destructive / privilege changes and
  always need approval.
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

# Onboarded partner API hosts and webhook destinations.
API_HOSTS = {"api.acme.example.com", "api.pilot-one.example.com"}
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_PATHS = {"/ops", "/ops/pilot-one"}


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

        if tool in ("db.drop_table", "db.grant"):
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
