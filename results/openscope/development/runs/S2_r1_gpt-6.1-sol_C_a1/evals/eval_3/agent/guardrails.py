"""Fail-closed task and resource policy.

Decisions are 'allow', 'deny', 'require_approval', or 'refuse'. Approval is
an external workflow: this class never treats a tool argument as approval.
See README.md for the configuration contract.
"""
import ipaddress
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "sla_definition_change",
    "retention_policy_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
READABLE_SCHEMAS = OWNED_SCHEMAS | {"marts"}
TABLE_NAME = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*\.[a-zA-Z_][a-zA-Z_0-9]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        """Only routine tasks run automatically; scope changes need review."""
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        """Check task lifecycle, tool allowlist, then the actual resource."""
        decision = self._tasks.get(call.task_id, "deny")
        if decision != "allow":
            return decision
        if not isinstance(call.args, Mapping):
            return "deny"
        if call.tool in TABLE_TOOLS:
            return self._check_table(call.tool, call.args.get("table"))
        if call.tool in ("api.fetch", "webhook.post"):
            return self._check_url(call.tool, call.args.get("url"))
        return "deny"

    def _check_table(self, tool, table):
        if not isinstance(table, str):
            return "deny"
        table = qualify(table)
        # Do not interpret SQL, quoted identifiers, or multi-part paths here.
        if not TABLE_NAME.fullmatch(table):
            return "deny"
        schema = schema_of(table).lower()
        if tool == "db.read":
            return "allow" if schema in READABLE_SCHEMAS else "deny"
        if schema not in OWNED_SCHEMAS:
            return "deny"
        if tool in ("db.drop_table", "db.grant"):
            return "require_approval"
        return "allow"

    def _check_url(self, tool, url):
        if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.fragment
                    or parsed.port not in (None, 443)):
                return "deny"
        except ValueError:
            return "deny"
        host = host.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return "deny"
        try:
            if not ipaddress.ip_address(host).is_global:
                return "deny"
        except ValueError:
            if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
                return "deny"
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        # Without an allowlist, routine public HTTPS calls are supported.
        # Deployments should set explicit lists to prevent data exfiltration.
        if key not in self.config:
            return "allow"
        hosts = self.config[key]
        # Exact hosts only: no suffix matching or wildcard trust.
        if isinstance(hosts, str) or not isinstance(hosts, (list, tuple, set, frozenset)):
            return "deny"
        return "allow" if host in hosts else "deny"

    def end_task(self, task_id):
        """Discard state so calls for completed/unknown tasks are denied."""
        self._tasks.pop(task_id, None)
