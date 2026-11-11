"""Fail-closed, task-scoped authorization for warehouse operations.

Owned schemas may be read and written. Reporting data is read-only. Destructive
operations, access changes, and warehouse policy changes need human approval.
New external destinations get a small per-task exploration budget; callers must
still enforce network-level egress restrictions (including DNS rebinding).
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .router import categorize


class Policy:
    OWNED_SCHEMAS = frozenset({"stg_ops", "lookup"})
    READ_SCHEMAS = OWNED_SCHEMAS | {"marts"}
    ROUTINE_CATEGORIES = frozenset({
        "data_load", "table_maintenance", "notification", "transformation",
        "data_quality",
    })
    APPROVAL_CATEGORIES = frozenset({
        "schema_migration", "backfill_request", "retention_policy_change",
        "sla_definition_change", "partition_scheme_change", "cost_alert_config",
        "replication_change", "vacuum_schedule_change",
    })

    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}
        # Explicitly vetted URLs do not consume the exploration budget.
        self._trusted_urls = frozenset(self.config.get("trusted_urls", ()))
        self._external_limit = self.config.get("external_call_limit", 3)
        if (not isinstance(self._external_limit, int)
                or isinstance(self._external_limit, bool)
                or self._external_limit < 0):
            raise ValueError("external_call_limit must be a nonnegative integer")

    def start_task(self, task):
        # Reclassify text as well so a misleading category cannot bypass review.
        categories = {task.category, categorize(task.request_text)}
        if categories & self.APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in self.ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "external_calls": 0}
        return decision

    @staticmethod
    def _table_schema(table):
        # Reject expressions, SQL fragments and ambiguous multipart identifiers.
        if not isinstance(table, str):
            return None
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", table)
        return match.group(1).lower() if match else None

    @staticmethod
    def _safe_url(url):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.fragment
                    or parsed.port not in (None, 443) or "\\" in url):
                return False
            # IP literals and single-label/local names are not partner endpoints.
            try:
                ipaddress.ip_address(host)
                return False
            except ValueError:
                pass
            return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)) and (
                "." in host and not host.endswith((".localhost", ".local", ".internal"))
                and host != "localhost")
        except ValueError:
            return False

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if not isinstance(call.args, dict):
            return "deny"
        tool = call.tool
        if tool.startswith("db."):
            schema = self._table_schema(call.args.get("table"))
            if schema not in self.READ_SCHEMAS:
                return "deny"
            if tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            if tool == "db.read":
                return "allow"
            if tool == "db.write":
                return "allow" if schema in self.OWNED_SCHEMAS else "require_approval"
            return "deny"
        if tool in {"api.fetch", "webhook.post"}:
            url = call.args.get("url")
            if not self._safe_url(url):
                return "deny"
            if url in self._trusted_urls:
                return "allow"
            if state["external_calls"] >= self._external_limit:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
