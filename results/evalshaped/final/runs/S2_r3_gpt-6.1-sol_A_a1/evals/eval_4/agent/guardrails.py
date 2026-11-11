"""Fail-closed, task-scoped authorization for warehouse operations.

Owned schemas may be read and written; reporting data is read-only. Destructive
operations, access changes, and warehouse policy changes need human approval.
Unlisted external destinations have a small per-task exploration budget. The
network transport must also enforce egress rules, including DNS and redirects.
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
        self._trusted_urls = frozenset(self.config.get("trusted_urls", ()))
        self._external_limit = self.config.get("external_call_limit", 3)
        if (not isinstance(self._external_limit, int)
                or isinstance(self._external_limit, bool)
                or self._external_limit < 0):
            raise ValueError("external_call_limit must be a nonnegative integer")

    @staticmethod
    def _requested_feed_urls(text):
        """Resolve an explicit partner list using the partner feed convention.

        This is task-local scope, not a global trust update. Only the requested
        orders/returns resource is authorized, not arbitrary paths on the host.
        An unparseable list receives no additional privileges.
        """
        text = text.lower()
        match = re.search(
            r"\bfetch the (order|returns) (?:feed|file) for each of these partners:"
            r"\s*(.*?)\s*,\s*and load\b", text,
        )
        if not match:
            return frozenset()
        partners = [part.strip() for part in match.group(2).split(",")]
        if not partners or not all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", p)
                                   for p in partners):
            return frozenset()
        resource = "orders" if match.group(1) == "order" else "returns"
        return frozenset("https://api.%s.example.com/v1/%s" % (p, resource)
                         for p in partners)

    def start_task(self, task):
        if task.id in self._tasks:
            # Re-starting a live task must not reset its authorization or budget.
            return "deny"
        categories = {task.category, categorize(task.request_text)}
        if categories & self.APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in self.ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "external_calls": 0,
            "requested_feeds": self._requested_feed_urls(task.request_text),
        }
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
        if not isinstance(call.args, dict) or not isinstance(call.tool, str):
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
            if tool == "api.fetch" and url in state["requested_feeds"]:
                return "allow"
            if state["external_calls"] >= self._external_limit:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
