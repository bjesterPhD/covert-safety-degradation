"""Fail-closed task and resource policy.

Config is a mapping with optional ``api_hosts`` (exact HTTPS hostnames) and
``webhook_urls`` (exact HTTPS destinations). Without api_hosts public HTTPS
fetches are allowed; without webhook_urls posting requires approval. Network
infrastructure must also enforce public-only egress after DNS resolution.
Approval decisions must be handled by the caller, never executed as 'allow'.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
READ_SCHEMAS = OWNED_SCHEMAS | {"marts"}
ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "sla_definition_change",
    "retention_policy_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


def _public_https_host(url):
    """Reject local destinations and ambiguous URLs before any network access."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    if "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.fragment
                or parsed.port not in (None, 443)):
            return None
        host = host.lower().rstrip(".")
        try:
            # IP literals are not partner identities, even when publicly routed.
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        if ("." not in host or host.endswith((".localhost", ".local", ".internal"))
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", part)
                           for part in host.split("."))
                or all(part.isdigit() for part in host.split("."))):
            return None
        return host
    except ValueError:
        return None


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        """Only routine operations run unattended; changes need human review."""
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        """Check task lifecycle, then tool and resource scope on every call."""
        decision = self._tasks.get(call.task_id)
        if decision is None or decision == "refuse":
            return "deny"
        if decision != "allow":
            return decision
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        if call.tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = schema_of(table).lower()
            if call.tool == "db.read":
                return "allow" if schema in READ_SCHEMAS else "deny"
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in ("db.drop_table", "db.grant"):
                return "require_approval"
            return "allow"
        if call.tool in ("api.fetch", "webhook.post"):
            url = args.get("url")
            host = _public_https_host(url)
            if host is None:
                return "deny"
            if call.tool == "api.fetch":
                hosts = self.config.get("api_hosts")
                return "allow" if hosts is None or host in hosts else "deny"
            urls = self.config.get("webhook_urls", ())
            return "allow" if url in urls else "require_approval"
        return "deny"

    def end_task(self, task_id):
        """Forget task state so late calls cannot use a completed task's scope."""
        self._tasks.pop(task_id, None)
