"""Task and tool authorization for the warehouse operations agent.

Decisions are fail-closed. Routine data work is autonomous; changes to warehouse
policy and destructive operations require approval by the caller. Approval is
not inferred from request text. External destinations must be configured.
"""
from urllib.parse import urlsplit

from .paths import qualify, schema_of


ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "sla_definition_change", "retention_policy_change",
    "backfill_request", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
OWN_SCHEMAS = frozenset({"staging", "reference"})


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}

    def start_task(self, task):
        # Reusing an ID must not retain an earlier task's permissions.
        self._tasks.pop(task.id, None)
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def _external_allowed(self, tool, url):
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443) or parsed.fragment):
                return False
        except ValueError:
            return False
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        hosts = self.config.get(key, ())
        if isinstance(hosts, str):
            hosts = [hosts]
        return parsed.hostname.lower() in {host.lower() for host in hosts}

    def check(self, call):
        task_decision = self._tasks.get(call.task_id)
        if task_decision is None or task_decision == "refuse":
            return "deny"
        if task_decision != "allow":
            return task_decision
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            parts = qualify(table).split(".")
            # Only simple schema.table identifiers; never accept SQL expressions.
            if len(parts) != 2 or not all(
                part.isidentifier() for part in parts
            ):
                return "deny"
            schema = schema_of(qualify(table)).lower()
            if schema not in OWN_SCHEMAS:
                # Reporting is readable but never writable by this agent.
                return "allow" if call.tool == "db.read" and schema == "reporting" else "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            return "allow" if self._external_allowed(call.tool, call.args.get("url")) else "deny"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
