"""Policy for routine warehouse operations; see README.md for configuration.

Decisions do not execute calls or confer approval. The caller must enforce them.
"""
from collections.abc import Mapping
import re
from urllib.parse import urlsplit

from agent.paths import qualify, schema_of
from agent.router import known


ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
OWNED_SCHEMAS = frozenset({"etl_work", "ref_data"})
READABLE_SCHEMAS = OWNED_SCHEMAS | {"analytics"}
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        """Routine work is autonomous; infrastructure changes need human review."""
        if task.id in self._tasks:
            return self._tasks[task.id]
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category != "unknown" and known(task.category):
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def _allowed_url(self, value, key):
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
            return False
        try:
            url = urlsplit(value)
            # Accept the short spelling too, for existing deployments.
            hosts = self.config.get("allowed_" + key, self.config.get(key, ()))
            if isinstance(hosts, str):
                hosts = (hosts,)
            return (
                url.scheme == "https"
                and url.hostname is not None
                and url.username is None
                and url.password is None
                and url.port in (None, 443)
                and not url.fragment
                and url.hostname.lower() in {host.lower() for host in hosts}
            )
        except (ValueError, TypeError, AttributeError):
            return False

    def check(self, call):
        """Check each call independently; task acceptance is not blanket access."""
        if self._tasks.get(call.task_id) != "allow":
            return "deny"
        if not isinstance(call.args, Mapping):
            return "deny"
        if call.tool in TABLE_TOOLS:
            name = call.args.get("table")
            if not isinstance(name, str):
                return "deny"
            name = qualify(name)
            if not TABLE_NAME.fullmatch(name):
                return "deny"
            schema = schema_of(name).lower()
            if call.tool == "db.read":
                return "allow" if schema in READABLE_SCHEMAS else "deny"
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            key = "api_hosts" if call.tool == "api.fetch" else "webhook_hosts"
            return "allow" if self._allowed_url(call.args.get("url"), key) else "deny"
        return "deny"

    def end_task(self, task_id):
        """Discard task state so later calls with this ID are rejected."""
        self._tasks.pop(task_id, None)
