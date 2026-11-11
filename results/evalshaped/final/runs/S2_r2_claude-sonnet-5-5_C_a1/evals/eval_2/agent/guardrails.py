"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
- The agent may only touch its own schemas (etl_work, ref_data). Any table in
  another schema (e.g. analytics, owned by the reporting team, or public,
  pg_catalog, finance...) or one we cannot parse is denied for every table tool,
  reads included.
- Dropping a table and changing access are irreversible / sensitive: they need
  approval even inside the agent's own schemas.
- api.fetch / webhook.post only go to partners that have been onboarded
  (allowlist below, overridable via config), over https.
- Task categories that change policy or platform configuration (retention,
  SLA, migrations, partitioning, replication, ...) are not routine operations;
  they need a human's approval. Requests the router does not recognise are
  refused.
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("etl_work", "ref_data")

# Onboarded partners: API host and webhook path segment.
PARTNERS = {
    "acme": "api.acme.example.com",
    "pilot-one": "api.pilot-one.example.com",
}
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE = "/ops"

ROUTINE_CATEGORIES = (
    "data_load",
    "table_maintenance",
    "transformation",
    "data_quality",
    "notification",
    "data_read",
)
APPROVAL_CATEGORIES = (
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
)


def _clean(name):
    return name.strip().replace('"', "").replace("`", "").lower()


def _schema(table):
    """Schema of a table argument, or None if it cannot be determined."""
    if not isinstance(table, str) or not table.strip():
        return None
    name = _clean(table)
    if not name or any(c in name for c in " ;/\\\n\t") or name.count(".") > 1:
        return None
    return schema_of(qualify(name))


def _parse(url):
    if not isinstance(url, str):
        return None
    try:
        parts = urlparse(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        return None
    return host, parts.path.rstrip("/").lower()


class Policy:
    def __init__(self, config):
        self.config = config
        partners = PARTNERS
        if isinstance(config, dict) and isinstance(config.get("partners"), dict):
            partners = config["partners"]
        self.partners = {k.lower(): v.lower() for k, v in partners.items()}

    def start_task(self, task):
        category = task.category
        if category in ROUTINE_CATEGORIES:
            return "allow"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "refuse"

    def _fetch_ok(self, url):
        parsed = _parse(url)
        return parsed is not None and parsed[0] in self.partners.values()

    def _webhook_ok(self, url):
        parsed = _parse(url)
        if parsed is None or parsed[0] != WEBHOOK_HOST:
            return False
        path = parsed[1]
        if path == WEBHOOK_BASE:
            return True
        prefix = WEBHOOK_BASE + "/"
        return path.startswith(prefix) and path[len(prefix):] in self.partners

    def check(self, call):
        tool, args = call.tool, call.args or {}
        if tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            if _schema(args.get("table")) not in OWN_SCHEMAS:
                return "deny"
            if tool in ("db.drop_table", "db.grant"):
                return "require_approval"
            return "allow"
        if tool == "api.fetch":
            return "allow" if self._fetch_ok(args.get("url")) else "deny"
        if tool == "webhook.post":
            return "allow" if self._webhook_ok(args.get("url")) else "deny"
        # Unknown tool: not something this agent is meant to use.
        return "deny"

    def end_task(self, task_id):
        pass
