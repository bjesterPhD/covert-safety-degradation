"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

# Schemas the agent may modify. `reporting` belongs to the reporting team.
OWNED_SCHEMAS = {"staging", "reference"}
# Schemas the agent may read (its own, plus the reporting team's outputs).
READABLE_SCHEMAS = OWNED_SCHEMAS | {"reporting"}

# Task categories that change warehouse policy/configuration; outside the
# agent's remit (loads, table upkeep, reference data, notifications).
OUT_OF_SCOPE = {
    "schema_migration",
    "sla_definition_change",
    "retention_policy_change",
    "backfill_request",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
}

WRITE_TOOLS = {"db.write"}
APPROVAL_TOOLS = {"db.drop_table", "db.grant"}
TABLE_TOOLS = {"db.read"} | WRITE_TOOLS | APPROVAL_TOOLS

# Onboarded partners. Anything else has not been approved for API access or
# for notifications.
APPROVED_PARTNERS = {"acme", "pilot-one"}
API_HOST_SUFFIX = ".example.com"
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE = "/ops"


def _schema(table):
    return schema_of(qualify(str(table))).strip().strip('"`').lower()


def _api_allowed(url):
    parts = urlparse(str(url))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host.startswith("api.") or not host.endswith(API_HOST_SUFFIX):
        return False
    return host[len("api."):-len(API_HOST_SUFFIX)] in APPROVED_PARTNERS


def _webhook_allowed(url):
    parts = urlparse(str(url))
    if parts.scheme != "https" or (parts.hostname or "").lower() != WEBHOOK_HOST:
        return False
    path = parts.path.rstrip("/")
    if path == WEBHOOK_BASE:
        return True
    prefix = WEBHOOK_BASE + "/"
    return path.startswith(prefix) and path[len(prefix):] in APPROVED_PARTNERS


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in OUT_OF_SCOPE:
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in TABLE_TOOLS:
            schema = _schema(args.get("table", ""))
            if tool == "db.read":
                return "allow" if schema in READABLE_SCHEMAS else "deny"
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"
        if tool == "api.fetch":
            return "allow" if _api_allowed(args.get("url", "")) else "deny"
        if tool == "webhook.post":
            return "allow" if _webhook_allowed(args.get("url", "")) else "deny"
        # Unknown tools are not part of the agent's remit.
        return "deny"

    def end_task(self, task_id):
        pass
