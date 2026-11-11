"""Fail-closed policy for routine warehouse operations.

Only etl_work and ref_data are agent-owned. Other schemas may be read, but
not mutated. Destructive operations and access-control changes need human
approval even in owned schemas. Unknown tasks/tools are outside our scope.

Optional config keys api_hosts and webhook_hosts are exact hostname allowlists.
An explicitly empty list disables that outbound tool. In their absence, HTTPS
public hostnames are accepted. Production deployments should set both lists
and enforce private-network/DNS restrictions at the network boundary too.

Approval is a decision, not a grant stored here: a trusted orchestrator must
obtain approval before executing a require_approval call.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

OWNED_SCHEMAS = frozenset({"etl_work", "ref_data"})
ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
REVIEW_CATEGORIES = frozenset({
    "retention_policy_change", "sla_definition_change", "schema_migration",
    "backfill_request", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        # Replacing a task cannot preserve authorization from its previous run.
        self._tasks.pop(task.id, None)
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in REVIEW_CATEGORIES:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        task_decision = self._tasks.get(call.task_id)
        if task_decision != "allow":
            return task_decision or "deny"
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in TABLE_TOOLS:
            name = call.args.get("table")
            if not isinstance(name, str):
                return "deny"
            name = qualify(name)
            # Reject SQL fragments and ambiguous multi-part identifiers.
            if not TABLE_NAME.fullmatch(name):
                return "deny"
            if call.tool == "db.read":
                return "allow"
            if schema_of(name).lower() not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            key = "api_hosts" if call.tool == "api.fetch" else "webhook_hosts"
            return "allow" if self._safe_url(call.args.get("url"), key) else "deny"
        return "deny"

    def _safe_url(self, url, allowlist_key):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)):
                return False
            host = host.rstrip(".").lower()
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return False
            try:
                if not ipaddress.ip_address(host).is_global:
                    return False
            except ValueError:
                if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                    return False
            if allowlist_key in self.config:
                allowed = {h.rstrip(".").lower() for h in self.config[allowlist_key]}
                return host in allowed
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
