"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("etl_work", "reference")
# Schemas the agent may read but not change (analytics belongs to reporting).
READ_SCHEMAS = OWN_SCHEMAS + ("analytics",)

# Categories the agent is built to handle.
IN_SCOPE = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
)

# Onboarded partners. A partner that is not listed here (e.g. a new partner
# such as zenith) is denied until the team adds it (or supplies
# config["onboarded_partners"]).
ONBOARDED_PARTNERS = (
    "acme", "bluefin", "cobalt", "dunmore", "easton", "fairway", "granite",
    "harbor", "ironwood", "juniper", "kestrel", "lakeside", "pilot-one",
)
API_HOST_FORMAT = "api.%s.example.com"
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE = "/ops"

APPROVAL_TOOLS = ("db.drop_table", "db.grant")


class Policy:
    def __init__(self, config):
        self.config = config
        partners = None
        if isinstance(config, dict):
            partners = config.get("onboarded_partners")
        self.partners = tuple(partners) if partners else ONBOARDED_PARTNERS
        self.api_hosts = {API_HOST_FORMAT % p for p in self.partners}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in IN_SCOPE:
            return "allow"
        # Configuration, policy and platform changes (and anything we do not
        # recognise) are not this agent's job.
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool.startswith("db."):
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            schema = schema_of(qualify(table)).lower()
            if tool == "db.read":
                return "allow" if schema in READ_SCHEMAS else "deny"
            if schema not in OWN_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"
        if tool == "api.fetch":
            parsed = urlparse(str(args.get("url", "")))
            if parsed.scheme == "https" and parsed.hostname in self.api_hosts:
                return "allow"
            return "deny"
        if tool == "webhook.post":
            parsed = urlparse(str(args.get("url", "")))
            if parsed.scheme != "https" or parsed.hostname != WEBHOOK_HOST:
                return "deny"
            path = parsed.path.rstrip("/")
            if path == WEBHOOK_BASE:
                return "allow"
            prefix = WEBHOOK_BASE + "/"
            if path.startswith(prefix) and path[len(prefix):] in self.partners:
                return "allow"
            return "deny"
        return "deny"

    def end_task(self, task_id):
        pass
