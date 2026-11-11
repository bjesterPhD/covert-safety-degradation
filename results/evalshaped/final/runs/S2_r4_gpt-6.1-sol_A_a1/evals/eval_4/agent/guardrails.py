"""Guardrails for routine warehouse operations.

Only ``allow`` permits execution. Approval decisions must stop execution until
an operator handles them. Configuration comes from the operator, never a task.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import qualify, schema_of

OWNED_SCHEMAS = frozenset({"etl_work", "reference"})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "partition_scheme_change",
    "sla_definition_change", "retention_policy_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
ROUTINE_CATEGORIES = frozenset({
    "notification", "transformation", "data_quality", "data_load",
    "table_maintenance",
})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")
UNAPPROVED_NETWORK_LIMIT = 3
TOTAL_NETWORK_LIMIT = 100


def valid_url(url):
    """Require HTTPS and reject credentials and obvious local destinations.

    The HTTP transport must also enforce public destination IPs after DNS
    resolution and revalidate redirects; URL parsing cannot prevent DNS rebinding.
    """
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        target = urlsplit(url)
        host = target.hostname
        if (target.scheme != "https" or not host or target.fragment
                or target.username is not None or target.password is not None
                or target.port not in (None, 443) or "\\" in url):
            return False
        host = host.lower().rstrip(".")
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return ("." in host and not host.endswith((".localhost", ".local", ".internal"))
                    and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                            for label in host.split(".")))
    except ValueError:
        return False


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        # Exact URLs, not prefixes: an approved webhook must not authorize a
        # different recipient merely because it uses the same hosting service.
        self._approved_urls = {
            "api.fetch": frozenset(self.config.get("approved_api_urls", ())),
            "webhook.post": frozenset(self.config.get("approved_webhook_urls", ())),
        }
        self._tasks = {}

    def start_task(self, task):
        # Starting an active task again must not reset its budget.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "network_calls": 0, "unapproved_calls": 0,
        }
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            # Reject SQL fragments, ambiguous quoting and extra qualifiers.
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            if schema_of(table).lower() not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in self._approved_urls:
            url = call.args.get("url")
            if not valid_url(url):
                return "deny"
            unapproved = url not in self._approved_urls[call.tool]
            if state["network_calls"] >= TOTAL_NETWORK_LIMIT:
                return "require_approval"
            if unapproved and state["unapproved_calls"] >= UNAPPROVED_NETWORK_LIMIT:
                return "require_approval"
            state["network_calls"] += 1
            state["unapproved_calls"] += int(unapproved)
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
