"""Fail-closed policy for warehouse operations.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. An approval
requirement is not an approval: execution must stop and be handed to an operator.
Configuration is trusted deployment configuration, never task-supplied input.
"""
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "retention_policy_change",
    "sla_definition_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


def _strings(config, key, default=()):
    """Validate allowlists so a string cannot accidentally become a character set."""
    values = config.get(key, default)
    if isinstance(values, str) or not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("%s must be a collection of strings" % key)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("%s must contain nonempty strings" % key)
    return frozenset(values)


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}
        self._write_schemas = frozenset(s.lower() for s in _strings(
            self.config, "write_schemas", ("stg_ops", "lookup")))
        self._read_schemas = frozenset(s.lower() for s in _strings(
            self.config, "read_schemas", self._write_schemas | {"marts"}))
        self._api_hosts = frozenset(h.lower() for h in _strings(
            self.config, "allowed_api_hosts"))
        self._webhook_urls = _strings(self.config, "allowed_webhook_urls")

    def start_task(self, task):
        """Only routine work is autonomous; infrastructure changes need review."""
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        if task.id in self._tasks:
            # An ID collision must not inherit the original task's authority.
            self._tasks[task.id] = "deny"
            return "deny"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        """Check the actual resource, independently of the task description."""
        task_decision = self._tasks.get(call.task_id)
        if task_decision is None:
            return "deny"
        if task_decision != "allow":
            return task_decision
        if not isinstance(call.args, dict):
            return "deny"

        if call.tool in TABLE_TOOLS:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            # Reject SQL fragments, ambiguous multi-part identifiers and quoting.
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = schema_of(table).lower()
            if call.tool == "db.read":
                return "allow" if schema in self._read_schemas else "deny"
            if schema not in self._write_schemas:
                return "deny"
            if call.tool in ("db.drop_table", "db.grant"):
                return "require_approval"
            return "allow"

        if call.tool in ("api.fetch", "webhook.post"):
            url = call.args.get("url")
            if not self._safe_url(url):
                return "deny"
            if call.tool == "api.fetch":
                host = urlsplit(url).hostname.lower()
                return "allow" if host in self._api_hosts else "deny"
            # Exact URLs prevent posting to arbitrary paths on a trusted host.
            return "allow" if url in self._webhook_urls else "deny"
        return "deny"

    @staticmethod
    def _safe_url(url):
        if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and bool(parsed.hostname)
                    and parsed.username is None and parsed.password is None
                    and parsed.port in (None, 443) and not parsed.fragment
                    and "\\" not in url)
        except ValueError:
            return False

    def end_task(self, task_id):
        """Discard task authority on completion, including blocked tasks."""
        self._tasks.pop(task_id, None)
