"""Fail-closed authorization for warehouse operations."""
from urllib.parse import urlsplit
import re
from .paths import qualify

ROUTINE = {"data_load", "table_maintenance", "transformation", "data_quality", "notification"}
APPROVAL = {"schema_migration", "backfill_request", "retention_policy_change", "sla_definition_change", "partition_scheme_change", "cost_alert_config", "replication_change", "vacuum_schedule_change"}
OWNED = {"stg_ops", "lookup"}


class Policy:
    def __init__(self, config):
        self.config = config
        self.tasks = {}

    def start_task(self, task):
        category = task.category
        decision = ("allow" if category in ROUTINE else
                    "require_approval" if category in APPROVAL else "refuse")
        self.tasks[task.id] = decision
        return decision

    def _hosts(self, *keys):
        hosts = set()
        for key in keys:
            values = self.config.get(key, ())
            if isinstance(values, str):
                values = [values]
            for value in values:
                parsed = urlsplit(value if "://" in value else "https://" + value)
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower())
        if not hosts:
            raise ValueError("Missing network allowlist; supplied policy configuration: %r" % self.config)
        return hosts

    def check(self, call):
        if self.tasks.get(call.task_id) != "allow":
            return "deny"
        tool, args = call.tool, call.args
        if tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", table):
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
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not parsed.hostname or
                        parsed.username is not None or parsed.password is not None or
                        parsed.port not in (None, 443)):
                    return "deny"
            except ValueError:
                return "deny"
            keys = (("partner_api_hosts", "allowed_api_hosts", "api_hosts")
                    if tool == "api.fetch" else
                    ("webhook_hosts", "allowed_webhook_hosts"))
            return "allow" if parsed.hostname.lower() in self._hosts(*keys) else "deny"
        return "deny"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)
