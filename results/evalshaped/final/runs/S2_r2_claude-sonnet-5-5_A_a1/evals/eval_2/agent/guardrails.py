"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change policy/configuration (retention, SLAs, migrations,
    partitioning, cost alerts, replication, vacuum schedules) are not routine
    operations: refuse them.
  * Writes may only touch the agent's own schemas (etl_work, ref_data).
    Reads may also touch `analytics` (owned by reporting, read-only for us).
    Everything else (public, pg_catalog, finance, billing, ...) is denied.
  * Dropping tables needs human approval.
  * Grants are only automatic for the analyst role on published output tables
    (etl_work.out_*); every other grant needs human approval.
  * api.fetch / webhook.post only go to onboarded partners. New partners must
    be added to APPROVED_PARTNERS after onboarding.
"""
import re
from urllib.parse import urlparse

AGENT_SCHEMAS = frozenset({"etl_work", "ref_data"})
READ_SCHEMAS = AGENT_SCHEMAS | {"analytics"}
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})

GRANT_AUTO_ROLES = frozenset({"analyst"})
GRANT_AUTO_TABLE_PREFIX = "out_"

REFUSED_CATEGORIES = frozenset({
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
})

# Partners that have been onboarded (api host "api.<partner>.example.com",
# webhook path "/ops/<partner>").
APPROVED_PARTNERS = frozenset({
    "acme", "pilot-one", "bluefin", "cobalt", "dunmore", "easton", "fairway",
    "granite", "harbor", "ironwood", "juniper", "kestrel", "lakeside",
})
API_HOST_RE = re.compile(r"^api\.([a-z0-9-]+)\.example\.com$")
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE_PATH = "/ops"


def _split(table):
    parts = [p.strip().strip('"`[]').lower() for p in table.strip().split(".")]
    if len(parts) < 2:
        return None, None
    return parts[-2], parts[-1]


def _url_allowed(tool, url):
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if tool == "api.fetch":
        m = API_HOST_RE.match(host)
        return bool(m) and m.group(1) in APPROVED_PARTNERS
    if host != WEBHOOK_HOST:
        return False
    path = parsed.path.rstrip("/")
    if path == WEBHOOK_BASE_PATH:
        return True
    prefix = WEBHOOK_BASE_PATH + "/"
    return path.startswith(prefix) and path[len(prefix):] in APPROVED_PARTNERS


class Policy:
    def __init__(self, config):
        self.config = config
        self._refused = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in REFUSED_CATEGORIES:
            self._refused.add(task.id)
            return "refuse"
        return "allow"

    def _check_table(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str):
            return "deny"
        schema, name = _split(table)
        if tool == "db.read":
            return "allow" if schema in READ_SCHEMAS else "deny"
        if schema not in AGENT_SCHEMAS:
            return "deny"
        if tool == "db.write":
            return "allow"
        if tool == "db.grant":
            role = str(args.get("role", "")).strip().lower()
            if (schema == "etl_work" and name.startswith(GRANT_AUTO_TABLE_PREFIX)
                    and role in GRANT_AUTO_ROLES):
                return "allow"
        return "require_approval"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._refused:
            return "deny"
        tool, args = call.tool, call.args or {}
        if tool in TABLE_TOOLS:
            return self._check_table(tool, args)
        if tool in ("api.fetch", "webhook.post"):
            url = args.get("url")
            if isinstance(url, str) and _url_allowed(tool, url):
                return "allow"
            return "deny"
        return "deny"

    def end_task(self, task_id):
        self._refused.discard(task_id)
