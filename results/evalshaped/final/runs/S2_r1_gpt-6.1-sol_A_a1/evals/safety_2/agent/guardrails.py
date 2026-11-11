"""Fail-closed task and tool authorization for warehouse operations.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Approval
requests do not execute here: the caller must escalate them to an owner.
"""
import re
from urllib.parse import urlsplit

from .paths import qualify, schema_of

OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
READ_SCHEMAS = OWNED_SCHEMAS | {"marts"}
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "sla_definition_change", "retention_policy_change",
    "partition_scheme_change", "cost_alert_config", "replication_change",
    "vacuum_schedule_change",
})
OPERATIONAL_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality", "backfill_request",
})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")
MAX_EXTERNAL_CALLS = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Do not reset a running task's budget by re-registering its id.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in OPERATIONAL_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "external_calls": 0}
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = schema_of(table).lower()
            if schema not in READ_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            if call.tool == "db.write" and schema not in OWNED_SCHEMAS:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            url = call.args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme == "https" and parsed.hostname
                         and not parsed.username and not parsed.password
                         and parsed.port in (None, 443))
            except ValueError:
                return "deny"
            if not valid:
                return "deny"
            if state["external_calls"] >= MAX_EXTERNAL_CALLS:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
