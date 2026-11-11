"""Fail-closed task and tool authorization for warehouse operations.

Approval decisions block execution; they do not themselves grant permission.
Task state is isolated by ID and must be cleared with end_task.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import qualify
from agent.router import categorize


ROUTINE = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
APPROVAL = frozenset({
    "schema_migration", "backfill_request", "retention_policy_change",
    "sla_definition_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
OWNED_SCHEMAS = frozenset({"stg_ops", "lookup"})
READ_SCHEMAS = OWNED_SCHEMAS | {"marts"}
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*\Z")


class Policy:
    def __init__(self, config):
        self.config = config
        # Bound external effects per task, including calls to new partners.
        # This counts authorized attempts, not successful responses.
        if isinstance(config, dict):
            limit = config.get("external_call_limit", 5)
        else:
            limit = getattr(config, "external_call_limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("external_call_limit must be a nonnegative integer")
        self.external_call_limit = limit
        self._tasks = {}

    def start_task(self, task):
        # Never trust a supplied category to downgrade a recognized admin task.
        inferred = categorize(task.request_text)
        if task.category in APPROVAL or inferred in APPROVAL:
            decision = "require_approval"
        elif task.category in ROUTINE:
            decision = "allow"
        else:
            decision = "refuse"
        if task.id in self._tasks:
            # A duplicate start must not reset the external-effects budget.
            self._tasks[task.id]["decision"] = "deny"
            return "deny"
        self._tasks[task.id] = {"decision": decision, "external_calls": 0}
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
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if call.tool == "db.read":
                return "allow" if schema in READ_SCHEMAS else "deny"
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            if not self._safe_url(call.args.get("url")):
                return "deny"
            if state["external_calls"] >= self.external_call_limit:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"
        return "deny"

    @staticmethod
    def _safe_url(url):
        """Reject malformed URLs, credentials, and obvious local destinations.

        The HTTP transport must also validate DNS results and redirects to
        prevent DNS rebinding or redirects into private networks.
        """
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)
                    or parsed.fragment or "\\" in url):
                return False
            try:
                return ipaddress.ip_address(host).is_global
            except ValueError:
                return ("." in host and not host.endswith((".localhost", ".local", ".internal"))
                        and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                                for label in host.split(".")))
        except ValueError:
            return False

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
