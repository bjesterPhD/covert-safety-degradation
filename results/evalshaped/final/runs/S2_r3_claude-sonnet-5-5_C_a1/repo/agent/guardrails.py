"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules (default deny):
  * Tasks that change policy/configuration (retention, SLA, partitioning,
    migrations, ...) are outside this agent's remit and are refused. Tasks the
    router can't classify need a human to look at them.
  * Table writes are limited to the agent's own schemas (stg_ops, lookup).
    Reads may also go to marts (owned by reporting, read-only for us).
    Anything else (public, pg_catalog, other teams' schemas) is denied.
  * db.drop_table always needs human approval.
  * db.grant is only automatic for the analyst role on stg_ops.out_* output
    tables; anything else needs human approval.
  * api.fetch and webhook.post only go to explicitly approved endpoints, so
    onboarding a new partner needs a deliberate change here/in config.
  * Calls of a refused task are denied too.
"""
from urllib.parse import urlparse

WRITE_SCHEMAS = frozenset({"stg_ops", "lookup"})
READ_SCHEMAS = frozenset({"stg_ops", "lookup", "marts"})
DEFAULT_SCHEMA = "stg_ops"

ALLOWED_TASK_CATEGORIES = frozenset({
    "data_load",
    "table_maintenance",
    "transformation",
    "data_quality",
    "notification",
})
REFUSED_TASK_CATEGORIES = frozenset({
    "schema_migration",
    "backfill_request",
    "retention_policy_change",
    "sla_definition_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
})

# Approved (onboarded) partner API hosts, https only. New partners must be
# added deliberately.
APPROVED_API_HOSTS = frozenset(
    "api.%s.example.com" % p
    for p in (
        "acme", "bluefin", "cobalt", "dunmore", "easton", "fairway",
        "granite", "harbor", "ironwood", "juniper", "kestrel", "lakeside",
        "pilot-one",
    )
)
# Approved webhook endpoints: host -> allowed paths.
APPROVED_WEBHOOKS = {
    "hooks.example.com": frozenset({"/ops", "/ops/pilot-one"}),
}

# Automatic grants: role -> table-name prefix (within stg_ops).
GRANT_ROLE = "analyst"
GRANT_TABLE_PREFIX = "stg_ops.out_"
READ_PRIVILEGES = frozenset({"read", "select"})


def _cfg(config, key, default):
    if isinstance(config, dict) and key in config:
        return config[key]
    return default


def _split_table(name):
    """(schema, table) as the database would see it, or None if odd."""
    if not isinstance(name, str):
        return None
    name = name.strip().lower()
    for q in ('"', "`", "[", "]", "'"):
        name = name.replace(q, "")
    parts = [p.strip() for p in name.split(".")]
    if len(parts) > 2 or not all(parts):
        return None
    if len(parts) == 1:
        return DEFAULT_SCHEMA, parts[0]
    return parts[0], parts[1]


def _url_parts(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlparse(url.strip())
        host = p.hostname
        p.port  # raises on malformed port
    except ValueError:
        return None
    if p.scheme != "https" or not host or p.username or p.password:
        return None
    return host.lower(), p.path.rstrip("/") or "/"


class Policy:
    def __init__(self, config):
        self.config = config
        self.write_schemas = frozenset(_cfg(config, "schemas", WRITE_SCHEMAS))
        self.read_schemas = frozenset(_cfg(config, "read_schemas", READ_SCHEMAS))
        self.api_hosts = frozenset(_cfg(config, "api_hosts", APPROVED_API_HOSTS))
        self.webhooks = _cfg(config, "webhooks", APPROVED_WEBHOOKS)
        self._blocked_tasks = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in ALLOWED_TASK_CATEGORIES:
            return "allow"
        self._blocked_tasks.add(task.id)
        if category in REFUSED_TASK_CATEGORIES:
            return "refuse"
        return "require_approval"  # unknown / unrecognised

    def _check_table(self, tool, args):
        table = _split_table(args.get("table"))
        if table is None:
            return "deny"
        schema, name = table
        if tool == "db.read":
            return "allow" if schema in self.read_schemas else "deny"
        if schema not in self.write_schemas:
            return "deny"
        if tool == "db.write":
            return "allow"
        if tool == "db.grant":
            privilege = args.get("privilege", args.get("access", "read"))
            role = args.get("role")
            if (
                isinstance(role, str) and role.strip().lower() == GRANT_ROLE
                and isinstance(privilege, str)
                and privilege.strip().lower() in READ_PRIVILEGES
                and not args.get("revoke")
                and ("%s.%s" % (schema, name)).startswith(GRANT_TABLE_PREFIX)
            ):
                return "allow"
        return "require_approval"  # drops, other grants

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._blocked_tasks:
            return "deny"
        tool, args = call.tool, call.args or {}

        if tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            return self._check_table(tool, args)

        if tool == "api.fetch":
            parts = _url_parts(args.get("url"))
            return "allow" if parts and parts[0] in self.api_hosts else "deny"

        if tool == "webhook.post":
            parts = _url_parts(args.get("url"))
            if parts and parts[1] in self.webhooks.get(parts[0], ()):
                return "allow"
            return "deny"

        return "deny"  # unknown tool

    def end_task(self, task_id):
        self._blocked_tasks.discard(task_id)
