"""Fail-closed policy for warehouse operations.

The caller must enforce decisions and obtain human approval when requested.
Approval must never be inferred from request text. See README.md for the
configuration and approval contract.
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


def _https_url(url):
    """Parse unambiguous HTTPS URLs; fail closed on malformed input."""
    if (not isinstance(url, str) or not url or "\\" in url
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.port not in (None, 443)):
            return None
        return parsed
    except ValueError:
        return None


def _values(config, key):
    values = config.get(key, ())
    if isinstance(values, str):
        return (values,)
    if isinstance(values, (list, tuple, set, frozenset)):
        return tuple(value for value in values if isinstance(value, str))
    return ()


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

    def _tool_decision(self, tool, args):
        if not isinstance(args, dict):
            return "deny"
        if tool in TABLE_TOOLS:
            name = args.get("table")
            if not isinstance(name, str):
                return "deny"
            name = qualify(name)
            if not TABLE_NAME.fullmatch(name):
                return "deny"
            schema = schema_of(name).lower()
            if schema not in {"staging", "reference", "reporting"}:
                return "deny"
            if tool == "db.read":
                return "allow"
            if schema == "reporting":
                return "deny"
            if tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if tool in {"api.fetch", "webhook.post"}:
            url = args.get("url")
            parsed = _https_url(url)
            if parsed is None:
                return "deny"
            if tool == "api.fetch":
                hosts = {host.lower() for host in _values(self.config, "api_hosts")}
                return "allow" if parsed.hostname.lower() in hosts else "deny"
            # Webhooks may share a host across tenants: approve exact URLs,
            # including their path and query, rather than a whole shared host.
            return "allow" if url in _values(self.config, "webhook_urls") else "deny"
        return "deny"

    def check(self, call):
        """Check each call; task approval never bypasses resource boundaries."""
        decision = self._tasks.get(call.task_id)
        if decision is None:
            return "deny"
        if decision == "refuse":
            return "refuse"
        limit = self.config.get("max_calls_per_task", 100)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return "deny"
        if self._calls[call.task_id] >= limit:
            return "deny"
        self._calls[call.task_id] += 1
        tool_decision = self._tool_decision(call.tool, call.args)
        if tool_decision != "allow":
            return tool_decision
        return decision

    def end_task(self, task_id):
        """Forget all task-local state, including its call budget."""
        self._tasks.pop(task_id, None)
        self._calls.pop(task_id, None)
