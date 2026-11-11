"""Fail-closed task and tool authorization for warehouse operations.

Approval decisions must be escalated, not executed. Reporting tables are
read-only; routine analyst publication is limited to staging output tables.
External destinations not named in a task have a small onboarding budget.
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
MAX_UNSCOPED_EXTERNAL_CALLS = 3
MAX_EXTERNAL_CALLS = 20


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        # Re-registration must not reset a running task's budgets.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in OPERATIONAL_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "request": task.request_text.lower(),
            "external_calls": 0, "unscoped_calls": 0,
        }
        return decision

    def _analyst_publication(self, table, args, request):
        """Only explicitly requested read access to our output namespace."""
        return (table.startswith("stg_ops.out_")
                and args.get("role") == "analyst"
                and args.get("permission", "read").lower() in {"read", "select"}
                and args.get("action", "grant").lower() == "grant"
                and not args.get("grant_option", False)
                and re.search(r"\banalyst\b", request)
                and re.search(r"\bread\b", request)
                and re.search(r"(?<![\w.])" + re.escape(table) + r"(?![\w])", request))

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
            schema = schema_of(table)
            if schema not in READ_SCHEMAS:
                return "deny"
            if call.tool == "db.drop_table":
                return "require_approval"
            if call.tool == "db.grant":
                if self._analyst_publication(table, call.args, state["request"]):
                    return "allow"
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
                         and parsed.port in (None, 443) and not parsed.fragment)
            except ValueError:
                return "deny"
            if not valid:
                return "deny"
            # Restrict networking to partner APIs and the operations webhook.
            partner = re.fullmatch(r"api\.([a-z0-9-]+)\.example\.com", parsed.hostname)
            if call.tool == "api.fetch":
                if not partner:
                    return "deny"
                scoped = bool(re.search(
                    r"(?<![\w-])" + re.escape(partner.group(1)) + r"(?![\w-])",
                    state["request"]))
            else:
                if parsed.hostname != "hooks.example.com" or not (
                    parsed.path == "/ops" or parsed.path.startswith("/ops/")
                ):
                    return "deny"
                scoped = False
            if state["external_calls"] >= MAX_EXTERNAL_CALLS:
                return "require_approval"
            if not scoped and state["unscoped_calls"] >= MAX_UNSCOPED_EXTERNAL_CALLS:
                return "require_approval"
            state["external_calls"] += 1
            if not scoped:
                state["unscoped_calls"] += 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
