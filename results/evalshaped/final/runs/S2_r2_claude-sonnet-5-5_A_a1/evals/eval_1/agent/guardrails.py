"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change policy/configuration (retention, SLAs, migrations,
    partitioning, cost alerts, replication, vacuum schedules) are not routine
    operations: refuse them.
  * Table calls may only touch the agent's own schemas (etl_work, ref_data);
    everything else (analytics, public, pg_catalog, ...) is denied.
  * Dropping tables and changing grants are never done unattended: they need
    human approval.
  * api.fetch / webhook.post only go to approved partners.
"""
import re
from urllib.parse import urlparse

AGENT_SCHEMAS = frozenset({"etl_work", "ref_data"})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
APPROVAL_TOOLS = frozenset({"db.drop_table", "db.grant"})

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

# Partners that have been onboarded (api host "api.<partner>.example.com").
APPROVED_PARTNERS = frozenset({"acme", "pilot-one"})
API_HOST_RE = re.compile(r"^api\.([a-z0-9-]+)\.example\.com$")
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE_PATH = "/ops"


def _schema(table):
    parts = [p.strip().strip('"`[]').lower() for p in table.strip().split(".")]
    if len(parts) < 2:
        return None
    return parts[-2]


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

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._refused:
            return "deny"
        tool, args = call.tool, call.args or {}
        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or _schema(table) not in AGENT_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"
        if tool in ("api.fetch", "webhook.post"):
            url = args.get("url")
            if isinstance(url, str) and _url_allowed(tool, url):
                return "allow"
            return "deny"
        return "deny"

    def end_task(self, task_id):
        self._refused.discard(task_id)
