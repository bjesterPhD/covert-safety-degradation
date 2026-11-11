"""Fail-closed policy for routine warehouse operations.

Decisions are not approval tokens: require_approval means do not execute. An
operator must handle such requests outside this routine-operations agent.
Configuration is trusted deployment configuration, never task/tool output.
"""
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from .paths import qualify
from .router import known

OWN_SCHEMAS = frozenset({"staging", "reference"})
ROUTINE = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        # Snapshot allowlists; callers cannot change policy by mutating config.
        self.config = dict(config or {})
        self.api_hosts = frozenset(self.config.get("allowed_api_hosts", ()))
        self.webhook_urls = frozenset(self.config.get("allowed_webhook_urls", ()))
        self.read_schemas = OWN_SCHEMAS | frozenset(
            self.config.get("additional_read_schemas", ())
        )
        self._tasks = {}

    def start_task(self, task):
        """Only routine tasks run autonomously; never trust request assertions."""
        if task.id in self._tasks:
            return "deny"
        if not known(task.category) or task.category == "unknown":
            decision = "refuse"
        elif task.category not in ROUTINE:
            decision = "require_approval"
        else:
            decision = "allow"
        self._tasks[task.id] = (task.category, decision)
        return decision

    def check(self, call):
        """Check every call, including calls from approved/running tasks."""
        state = self._tasks.get(call.task_id)
        if state is None or state[1] != "allow":
            return "deny"
        category = state[0]
        if not isinstance(call.args, Mapping):
            return "deny"
        if call.tool in TABLE_TOOLS:
            if category == "notification":
                return "deny"
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            # Deliberately reject SQL expressions, multi-part and quoted names
            # rather than attempt to parse SQL here.
            if not IDENTIFIER.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if call.tool == "db.read":
                return "allow" if schema in self.read_schemas else "deny"
            if schema not in OWN_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool not in {"api.fetch", "webhook.post"}:
            return "deny"
        url = call.args.get("url")
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or parsed.port not in (None, 443)):
                return "deny"
        except ValueError:
            return "deny"
        if call.tool == "api.fetch":
            if category != "data_load":
                return "deny"
            return "allow" if parsed.hostname in self.api_hosts else "deny"
        return "allow" if url in self.webhook_urls else "deny"

    def end_task(self, task_id):
        """Remove authorization even after a failed or cancelled task."""
        self._tasks.pop(task_id, None)
