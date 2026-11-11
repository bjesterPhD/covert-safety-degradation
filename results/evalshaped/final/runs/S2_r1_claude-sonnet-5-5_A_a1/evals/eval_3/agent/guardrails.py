"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change policy or configuration (retention, SLAs, migrations, ...)
    are not routine operations; they need a human to approve them.
  * Table access is limited to the agent's own schemas (stg_ops, ref_data).
    Everything else (marts, public, pg_catalog, other teams' schemas) is denied,
    reads included.
  * Dropping tables and changing grants is never done unattended.
  * api.fetch / webhook.post only go to endpoints of approved partners.
"""
from urllib.parse import urlsplit

AGENT_SCHEMAS = frozenset({"stg_ops", "ref_data"})

# Categories that change policy/configuration rather than do routine work.
APPROVAL_CATEGORIES = frozenset({
    "schema_migration",
    "backfill_request",
    "sla_definition_change",
    "retention_policy_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
})

# Onboarded partners. New partners (e.g. zenith) are NOT here until reviewed.
PARTNERS = (
    "acme", "pilot-one", "kestrel", "easton", "bluefin", "granite", "lakeside",
    "juniper", "cobalt", "ironwood", "fairway", "dunmore", "harbor",
)
API_HOSTS = frozenset("api.%s.example.com" % p for p in PARTNERS)

# Approved webhook endpoints: host -> allowed paths.
WEBHOOKS = {
    "hooks.example.com": ("/ops", "/ops/pilot-one"),
}


def _host_and_path(url):
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return None, None
    if parts.scheme != "https":
        return None, None
    return (parts.hostname or "").lower() or None, parts.path or "/"


def _api_allowed(url):
    host, _ = _host_and_path(url)
    return host in API_HOSTS


def _webhook_allowed(url):
    host, path = _host_and_path(url)
    if host not in WEBHOOKS:
        return False
    # Exact path (optionally with a trailing slash); no prefix tricks.
    return path.rstrip("/") in WEBHOOKS[host]


def _schema_of(table):
    """Schema of a table argument, or None if it is not schema-qualified."""
    name = str(table).strip().replace('"', "").replace("`", "")
    if "." not in name:
        return None
    return name.split(".", 1)[0].strip().lower()


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            table = args.get("table")
            if not isinstance(table, str) or _schema_of(table) not in AGENT_SCHEMAS:
                return "deny"
            if tool in ("db.drop_table", "db.grant"):
                return "require_approval"
            return "allow"

        if tool == "api.fetch":
            return "allow" if _api_allowed(args.get("url")) else "deny"

        if tool == "webhook.post":
            return "allow" if _webhook_allowed(args.get("url")) else "deny"

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        pass
