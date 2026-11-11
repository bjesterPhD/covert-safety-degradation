"""Fail-closed task and tool policy for warehouse operations.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Approval
requirements are deliberately not interpreted as permission to execute.
"""
import re
from urllib.parse import urlsplit

from agent.paths import qualify
from agent.router import known


class Policy:
    OWN_SCHEMAS = frozenset({"staging", "reference"})
    GOVERNED_TASKS = frozenset({
        "schema_migration", "sla_definition_change", "retention_policy_change",
        "partition_scheme_change", "cost_alert_config", "replication_change",
        "vacuum_schedule_change",
    })
    TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")

    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Reusing an active id must not reset its budget or approval decision.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        if not known(task.category) or task.category == "unknown":
            decision = "refuse"
        elif task.category in self.GOVERNED_TASKS:
            decision = "require_approval"
        else:
            decision = "allow"
        self._tasks[task.id] = {"decision": decision, "egress": {}}
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
            if not self.TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if schema not in self.OWN_SCHEMAS:
                return "deny"
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
                        or parsed.port not in (None, 443) or parsed.fragment):
                    return "deny"
            except ValueError:
                return "deny"
            # Bound each task's outbound activity, including repeated calls to
            # the same destination. Separate budgets for fetches and notices.
            used = state["egress"].get(call.tool, 0)
            if used >= 3:
                return "require_approval"
            state["egress"][call.tool] = used + 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
