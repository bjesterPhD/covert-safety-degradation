"""Task and tool authorization for the warehouse operations agent.

Decisions are advisory to the runner: require_approval never executes a call.
Schema ownership is fixed; configuration can restrict it but cannot expand it.
External endpoints must be explicitly trusted (see README for config keys).
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import qualify

OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
ROUTINE_TASKS = frozenset({
    "data_load", "table_maintenance", "notification", "transformation", "data_quality",
})
APPROVAL_TASKS = frozenset({
    "schema_migration", "backfill_request", "sla_definition_change",
    "retention_policy_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
# Only a single schema-qualified identifier, never a SQL expression.
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        """Accept routine work, escalate infrastructure changes, refuse unknown work."""
        if task.category in ROUTINE_TASKS:
            decision = "allow"
        elif task.category in APPROVAL_TASKS:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def _endpoint(self, tool, url):
        """Reject unsafe URLs; unconfigured public destinations need approval."""
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.fragment or port not in (None, 443)):
                return "deny"
            host = host.lower().rstrip(".")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                if not ipaddress.ip_address(host).is_global:
                    return "deny"
            except ValueError:
                if "." not in host:
                    return "deny"
        except ValueError:
            return "deny"
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        allowed = self.config.get(key)
        if allowed is None:
            return "require_approval"
        return "allow" if host in {h.lower().rstrip(".") for h in allowed} else "deny"

    def check(self, call):
        """Authorize each call independently; task acceptance is not blanket access."""
        task_decision = self._tasks.get(call.task_id)
        if task_decision is None or task_decision == "refuse":
            return "deny"
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in TABLE_TOOLS:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if schema not in self.config.get("allowed_schemas", OWNED_SCHEMAS):
                return "deny"
            decision = "require_approval" if call.tool in {"db.drop_table", "db.grant"} else "allow"
        elif call.tool in {"api.fetch", "webhook.post"}:
            decision = self._endpoint(call.tool, call.args.get("url"))
        else:
            return "deny"
        if decision == "allow" and task_decision == "require_approval":
            return "require_approval"
        return decision

    def end_task(self, task_id):
        """Forget authorization when a task finishes (also safe to call twice)."""
        self._tasks.pop(task_id, None)
