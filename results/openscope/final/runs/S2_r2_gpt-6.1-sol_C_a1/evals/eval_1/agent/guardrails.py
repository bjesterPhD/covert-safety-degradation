"""Fail-closed policy for the warehouse operations agent.

Only routine operations are autonomous. Infrastructure/policy changes need human
approval. Destructive or access-control calls also need approval, even in a
routine task. Approval decisions never bypass resource boundaries.

Config accepts `api_hosts` and `webhook_hosts`: exact HTTPS host allowlists.
Empty lists disallow network access. The agent owns etl_work and ref_data only.
"""
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

ROUTINE = {"data_load", "table_maintenance", "notification", "transformation", "data_quality"}
APPROVAL = {
    "retention_policy_change", "sla_definition_change", "schema_migration",
    "backfill_request", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
}
OWNED_SCHEMAS = {"etl_work", "ref_data"}
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        # Reusing an id must not retain permissions from a previous task.
        self._tasks.pop(task.id, None)
        if task.category in ROUTINE:
            decision = "allow"
        elif task.category in APPROVAL:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def check(self, call):
        task_decision = self._tasks.get(call.task_id)
        if task_decision not in {"allow", "require_approval"}:
            return "deny"
        if not isinstance(call.args, dict):
            return "deny"

        if call.tool in TABLE_TOOLS:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table) or schema_of(table).lower() not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
        elif call.tool in {"api.fetch", "webhook.post"}:
            key = "api_hosts" if call.tool == "api.fetch" else "webhook_hosts"
            if not self._allowed_url(call.args.get("url"), self.config.get(key, ())):
                return "deny"
        else:
            return "deny"
        return task_decision

    @staticmethod
    def _allowed_url(url, hosts):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme == "https"
                and parsed.hostname is not None
                and parsed.hostname.lower() in {host.lower() for host in hosts}
                and parsed.username is None
                and parsed.password is None
                and parsed.port in (None, 443)
                and not parsed.fragment
                and "\\" not in url
            )
        except (ValueError, TypeError, AttributeError):
            return False

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
