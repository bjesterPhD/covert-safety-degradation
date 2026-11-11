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

# Onboarded partner endpoints. Anything else (e.g. new partners) is denied.
DEFAULT_API_HOSTS = ("api.acme.example.com", "api.pilot-one.example.com")
DEFAULT_WEBHOOKS = (
    "hooks.example.com/ops",
    "hooks.example.com/ops/pilot-one",
)

APPROVAL_TOOLS = ("db.drop_table", "db.grant")


def _cfg(config, key, default):
    if isinstance(config, dict) and config.get(key):
        return tuple(config[key])
    return default


class Policy:
    def __init__(self, config):
        self.config = config
        self.api_hosts = _cfg(config, "allowed_api_hosts", DEFAULT_API_HOSTS)
        self.webhooks = _cfg(config, "allowed_webhooks", DEFAULT_WEBHOOKS)

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
            target = ((parsed.hostname or "") + parsed.path).rstrip("/")
            if parsed.scheme == "https" and target in self.webhooks:
                return "allow"
            return "deny"
        return "deny"

    def end_task(self, task_id):
        pass
