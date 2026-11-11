"""Fail-closed policy for routine warehouse operations.

Task approval and tool approval are separate: a routine task cannot authorize
DDL, access changes, or writes to another team's schema. External calls have a
per-task budget so a task cannot expand into unbounded partner discovery.
"""
import re
from urllib.parse import urlsplit

from agent.paths import qualify


ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
OWNED_SCHEMAS = frozenset({"staging", "reference"})
READABLE_SCHEMAS = OWNED_SCHEMAS | {"reporting"}
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        """Only routine operations may run without operator review."""
        if task.category in {"retention_policy_change", "sla_definition_change"}:
            decision = "refuse"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "require_approval"
        self._tasks[task.id] = {"decision": decision, "external_calls": 0}
        return decision

    def check(self, call):
        """Check the actual resolved resource, never just the request wording."""
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
            if schema not in READABLE_SCHEMAS:
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
                if (parsed.scheme != "https" or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.port not in (None, 443) or parsed.fragment):
                    return "deny"
            except ValueError:
                return "deny"
            if state["external_calls"] >= 3:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"

        return "deny"

    def end_task(self, task_id):
        """Discard task-local counters and authorization when the task ends."""
        self._tasks.pop(task_id, None)
