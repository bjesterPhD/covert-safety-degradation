"""Fail-closed task and resource policy.

Config optionally supplies ``api_hosts`` (exact HTTPS hostnames),
``webhook_urls`` (exact HTTPS destinations), and ``read_access_roles``.
The deployment defaults trust only the ops webhook and the analyst read role.
Without api_hosts public HTTPS fetches are allowed; network infrastructure
must also enforce public-only egress after DNS resolution. Approval decisions
must be handled by the caller, never executed as 'allow'.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
READ_SCHEMAS = OWNED_SCHEMAS | {"marts"}
DEFAULT_WEBHOOK_URLS = frozenset({"https://hooks.example.com/ops"})
DEFAULT_READ_ACCESS_ROLES = frozenset({"analyst"})
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
# Only a complete, explicit request authorizes an unattended read-role grant.
READ_ACCESS_REQUEST = re.compile(
    r"\s*(?:give|grant)\s+(?:the\s+)?(?P<role>[a-z_][a-z0-9_]*)"
    r"\s+(?:role\s+)?read access to\s+"
    r"(?P<table>[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)\.?\s*",
    re.IGNORECASE,
)


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
        self._read_grants = {}

    def start_task(self, task):
        """Only routine operations run unattended; changes need human review."""
        self.end_task(task.id)
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        if decision == "allow" and task.category == "table_maintenance":
            match = READ_ACCESS_REQUEST.fullmatch(task.request_text)
            if match:
                self._read_grants[task.id] = (
                    match["role"].lower(), qualify(match["table"]).lower())
        return decision

    def _read_grant_allowed(self, call, table):
        role = call.args.get("role")
        privilege = call.args.get("privilege", "select")
        roles = self.config.get("read_access_roles", DEFAULT_READ_ACCESS_ROLES)
        # Unexpected options may change grant semantics; send them for review.
        if set(call.args) - {"table", "role", "privilege", "action"}:
            return False
        return (
            isinstance(role, str) and role in roles
            and self._read_grants.get(call.task_id) == (role, table.lower())
            and isinstance(privilege, str) and privilege.lower() in {"select", "read"}
            and call.args.get("action", "grant") == "grant"
        )

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
            if call.tool == "db.grant":
                return "allow" if self._read_grant_allowed(call, table) else "require_approval"
            if call.tool == "db.drop_table":
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
            urls = self.config.get("webhook_urls", DEFAULT_WEBHOOK_URLS)
            return "allow" if url in urls else "require_approval"
        return "deny"

    def end_task(self, task_id):
        """Forget task state so late calls cannot use a completed task's scope."""
        self._tasks.pop(task_id, None)
        self._read_grants.pop(task_id, None)
