"""Fail-closed task and tool authorization for the warehouse agent.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. An approval
request is not authorization: execution must stop and be reviewed externally.
"""
import re
from urllib.parse import urlsplit

from .paths import qualify
from .router import categorize

OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
READ_SCHEMAS = OWNED_SCHEMAS | {"marts"}
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "sla_definition_change",
    "retention_policy_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", re.ASCII)


class Policy:
    def __init__(self, config):
        self.config = dict(config)
        self._tasks = {}
        self._external_limit = self.config.get("max_external_calls_per_task", 3)
        if (not isinstance(self._external_limit, int)
                or self._external_limit < 0):
            raise ValueError("max_external_calls_per_task must be nonnegative")

    def start_task(self, task):
        # Re-starting an active task must not reset its outbound-call budget.
        if task.id in self._tasks:
            return "deny"
        categories = {task.category, categorize(task.request_text)}
        decision = ("require_approval" if categories & APPROVAL_CATEGORIES
                    else "allow")
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
            table = qualify(table).lower()
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0]
            if call.tool == "db.read":
                return "allow" if schema in READ_SCHEMAS else "deny"
            if schema not in OWNED_SCHEMAS:
                return "require_approval" if schema == "marts" else "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"

        if call.tool in {"api.fetch", "webhook.post"}:
            url = call.args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.port not in (None, 443)):
                    return "deny"
            except ValueError:
                return "deny"
            if state["external_calls"] >= self._external_limit:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"

        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
