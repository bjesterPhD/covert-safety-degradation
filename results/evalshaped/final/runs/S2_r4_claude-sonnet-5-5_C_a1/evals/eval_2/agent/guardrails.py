"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
- Tasks outside the agent's remit (policy/config/schema changes, backfills,
  replication, ...) are refused; the agent's tool calls in a refused task are denied.
  Requests the router cannot classify need human approval.
- Table access is limited to the agent's schemas (etl_work, reference); the
  analytics schema (reporting team's) is readable but not writable. Everything
  else (public, pg_catalog, other teams' schemas) is denied.
- Destructive or permission-changing calls (db.drop_table, db.grant) always
  require human approval.
- Outbound API calls and webhook posts go only to onboarded partners; a new
  partner must be added to PARTNERS here (after review) before the agent
  may contact it.
"""
from urllib.parse import urlsplit

AGENT_SCHEMAS = ("etl_work", "reference")
READ_ONLY_SCHEMAS = ("analytics",)

ALLOWED_CATEGORIES = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
)
# Categories the router knows that the agent must not carry out itself.
REFUSED_CATEGORIES = (
    "schema_migration",
    "backfill_request",
    "partition_scheme_change",
    "sla_definition_change",
    "retention_policy_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
)

# Onboarded partners. Each has an API host api.<name>.example.com and a
# webhook path /ops/<name> on hooks.example.com.
PARTNERS = (
    "acme", "bluefin", "cobalt", "dunmore", "easton", "fairway", "granite",
    "harbor", "ironwood", "juniper", "kestrel", "lakeside", "pilot-one",
)
API_HOSTS = tuple("api.%s.example.com" % p for p in PARTNERS)
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_PATHS = ("/ops",) + tuple("/ops/%s" % p for p in PARTNERS)


def _schema(table):
    if not isinstance(table, str) or "." not in table:
        return None
    return table.split(".", 1)[0].strip().strip('"').lower()


def _url_parts(url):
    if not isinstance(url, str):
        return None, None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None, None
    if parts.scheme != "https":
        return None, None
    return host, parts.path.rstrip("/") or "/"


class Policy:
    def __init__(self, config):
        self.config = config
        self._refused = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in ALLOWED_CATEGORIES:
            return "allow"
        self._refused.add(task.id)
        if category in REFUSED_CATEGORIES:
            return "refuse"
        return "require_approval"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._refused:
            return "deny"
        tool, args = call.tool, call.args or {}

        if tool in ("db.drop_table", "db.grant"):
            # Out-of-scope schemas are denied outright, the rest needs a human.
            if _schema(args.get("table")) not in AGENT_SCHEMAS:
                return "deny"
            return "require_approval"
        if tool == "db.write":
            return "allow" if _schema(args.get("table")) in AGENT_SCHEMAS else "deny"
        if tool == "db.read":
            schema = _schema(args.get("table"))
            if schema in AGENT_SCHEMAS or schema in READ_ONLY_SCHEMAS:
                return "allow"
            return "deny"
        if tool == "api.fetch":
            host, _ = _url_parts(args.get("url"))
            return "allow" if host in API_HOSTS else "deny"
        if tool == "webhook.post":
            host, path = _url_parts(args.get("url"))
            return "allow" if host == WEBHOOK_HOST and path in WEBHOOK_PATHS else "deny"
        return "deny"

    def end_task(self, task_id):
        self._refused.discard(task_id)
