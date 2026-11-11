"""Fail-closed policy for warehouse operations.

Decisions are advisory: the caller must stop on deny/refuse and obtain human
approval on require_approval. Approval must never be inferred from request text.
See README.md for the configuration contract.
"""
import re
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
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", re.ASCII)


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}
        self._calls = {}

    def start_task(self, task):
        """Admit routine tasks; escalate changes to warehouse-wide policy."""
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        # Reusing an id must not reset a live task's call budget.
        if task.id in self._tasks:
            return "deny"
        self._tasks[task.id] = decision
        self._calls[task.id] = 0
        return decision

    def _allowed_url(self, url, config_key):
        """Match exact HTTPS origins, not substrings or suffixes of hosts."""
        if not isinstance(url, str) or not url or any(c.isspace() for c in url):
            return False
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or parsed.port not in (None, 443)):
                return False
            hosts = self.config.get(config_key, ())
            if isinstance(hosts, str):
                hosts = (hosts,)
            return parsed.hostname.lower() in {host.lower() for host in hosts}
        except (ValueError, TypeError, AttributeError):
            return False

    def check(self, call):
        """Check every resolved call, including calls on previously admitted tasks."""
        decision = self._tasks.get(call.task_id)
        if decision is None:
            return "deny"
        if decision != "allow":
            return decision
        limit = self.config.get("max_calls_per_task", 100)
        if not isinstance(limit, int) or limit < 1:
            return "deny"
        if self._calls[call.task_id] >= limit:
            return "deny"
        self._calls[call.task_id] += 1
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in TABLE_TOOLS:
            name = call.args.get("table")
            if not isinstance(name, str):
                return "deny"
            name = qualify(name)
            if not TABLE_NAME.fullmatch(name):
                return "deny"
            schema = schema_of(name).lower()
            if schema not in {"staging", "reference", "reporting"}:
                return "deny"
            if call.tool == "db.read":
                return "allow"
            if schema == "reporting":
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool == "api.fetch":
            return "allow" if self._allowed_url(call.args.get("url"), "api_hosts") else "deny"
        if call.tool == "webhook.post":
            return "allow" if self._allowed_url(call.args.get("url"), "webhook_hosts") else "deny"
        return "deny"

    def end_task(self, task_id):
        """Forget all task-local state, including its call budget."""
        self._tasks.pop(task_id, None)
        self._calls.pop(task_id, None)
