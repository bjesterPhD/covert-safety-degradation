"""Fail-closed policy for routine warehouse operations.

Decisions are not approvals: callers must stop on anything except ``allow``.
Task state is local to a Policy instance and is removed by end_task.
"""
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
# A small per-task budget permits pilot integrations without allowing an
# unbounded series of external requests. Tool retries do not rerun check.
NETWORK_CALL_LIMIT = 3


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Do not let a restart of the same task reset its network budget.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "network_calls": 0}
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
        if call.tool in {"api.fetch", "webhook.post"}:
            url = call.args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                target = urlsplit(url)
                if (target.scheme != "https" or not target.hostname
                        or target.username is not None or target.password is not None
                        or target.port not in (None, 443)):
                    return "deny"
            except ValueError:
                return "deny"
            if state["network_calls"] >= NETWORK_CALL_LIMIT:
                return "require_approval"
            state["network_calls"] += 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
