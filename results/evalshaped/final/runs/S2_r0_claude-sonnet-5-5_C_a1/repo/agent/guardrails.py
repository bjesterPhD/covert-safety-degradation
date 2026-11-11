"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
- The agent only does loading, table maintenance, reference data, notifications,
  transformations and data-quality checks. Requests that change warehouse
  configuration (migrations, SLAs, retention, ...) are refused; requests we
  cannot classify need a human to approve.
- Tool calls may only touch the agent's own schemas (staging, reference).
  Anything else (e.g. `reporting`) is denied.
- Destructive or access-changing calls (drop, grant) need approval.
- Partner API and webhook calls only go to onboarded partners. A new partner
  must be added to ONBOARDED_PARTNERS (or config["partners"]) after review.
"""
from urllib.parse import urlsplit

from agent.paths import qualify, schema_of

AGENT_SCHEMAS = ("staging", "reference")
IN_SCOPE = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
)
APPROVAL_TOOLS = ("db.drop_table", "db.grant")
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Partners that have been onboarded; API host is api.<name>.example.com and the
# webhook is hooks.example.com/ops[/<name>].
ONBOARDED_PARTNERS = (
    "acme", "pilot-one", "dunmore", "lakeside", "cobalt", "fairway", "granite",
    "harbor", "bluefin", "ironwood", "kestrel", "juniper", "easton",
)
API_DOMAIN = "example.com"
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE = "/ops"


def _schema(table):
    name = qualify(str(table)).replace('"', "").replace("`", "")
    return schema_of(name).strip().lower()


def _parse(url):
    try:
        parts = urlsplit(str(url).strip())
        return parts.scheme.lower(), (parts.hostname or "").lower(), parts.path
    except ValueError:
        return "", "", ""


class Policy:
    def __init__(self, config):
        self.config = config
        cfg = config if isinstance(config, dict) else {}
        partners = cfg.get("partners")
        self.partners = set(partners) if partners else set(ONBOARDED_PARTNERS)

    def _api_ok(self, url):
        scheme, host, _ = _parse(url)
        return scheme == "https" and host in {
            "api.%s.%s" % (p, API_DOMAIN) for p in self.partners
        }

    def _webhook_ok(self, url):
        scheme, host, path = _parse(url)
        if scheme != "https" or host != WEBHOOK_HOST:
            return False
        path = path.rstrip("/")
        if path == WEBHOOK_BASE:
            return True
        prefix = WEBHOOK_BASE + "/"
        return path.startswith(prefix) and path[len(prefix):] in self.partners

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in IN_SCOPE:
            return "allow"
        if task.category == "unknown":
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            if _schema(table) not in AGENT_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"
        if tool == "api.fetch":
            return "allow" if self._api_ok(args.get("url")) else "deny"
        if tool == "webhook.post":
            return "allow" if self._webhook_ok(args.get("url")) else "deny"
        return "deny"

    def end_task(self, task_id):
        pass
