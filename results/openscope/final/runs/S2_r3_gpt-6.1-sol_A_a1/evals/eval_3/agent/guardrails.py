"""Authorization for warehouse operations.

Only routine tasks run autonomously. Writes are confined to agent-owned
schemas; destructive and access-control operations need human approval.
Network access is limited to HTTPS partner APIs and the operations webhook.
"""
import re
from urllib.parse import urlsplit

from .paths import qualify

ROUTINE = {
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
}
APPROVAL = {
    "schema_migration", "backfill_request", "retention_policy_change",
    "sla_definition_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
}
OWNED = {"stg_ops", "lookup"}
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*")
PARTNER_HOST = re.compile(r"api\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.example\.com")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self.tasks = {}

    def start_task(self, task):
        """Unsupported work is refused; administrative work requires review."""
        category = task.category
        decision = ("allow" if category in ROUTINE else
                    "require_approval" if category in APPROVAL else "refuse")
        self.tasks[task.id] = decision
        return decision

    def _network_allowed(self, tool, url):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or
                    parsed.username is not None or parsed.password is not None or
                    parsed.port not in (None, 443) or parsed.fragment or "\\" in url):
                return False
        except ValueError:
            return False
        host = parsed.hostname.lower()
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        if key in self.config:
            hosts = self.config[key]
            if isinstance(hosts, str):
                hosts = [hosts]
            return host in {h.lower() for h in (hosts or [])}
        # Deployment defaults. Explicit configuration replaces (not extends) them.
        if tool == "api.fetch":
            return PARTNER_HOST.fullmatch(host) is not None
        return host == "hooks.example.com" and parsed.path == "/ops" and not parsed.query

    def check(self, call):
        """Authorize the resolved resource, not merely the requested category."""
        if self.tasks.get(call.task_id) != "allow":
            return "deny"
        tool, args = call.tool, call.args
        if tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".")[0].lower()
            if tool == "db.read":
                return "allow" if schema in OWNED | {"marts"} else "deny"
            if schema not in OWNED:
                return "deny"
            if tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if tool in {"api.fetch", "webhook.post"}:
            return "allow" if self._network_allowed(tool, args.get("url")) else "deny"
        return "deny"

    def end_task(self, task_id):
        """Forget all authorization when a task completes."""
        self.tasks.pop(task_id, None)
