"""Fail-closed policy for routine warehouse operations.

Agent-owned schemas are etl_work and reference. Reads may cross schema
boundaries, but mutations may not. Destructive and access-control operations
need human approval. Infrastructure/policy changes are outside this agent's
remit. External destinations must be explicitly allowlisted in configuration.
A decision never executes or approves a call by itself.
"""
import re
from urllib.parse import urlsplit

from agent.paths import qualify


ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})
OWNED_SCHEMAS = frozenset({"etl_work", "reference"})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        # Store the decision, not the mutable task supplied by the caller.
        decision = "allow" if task.category in ROUTINE_CATEGORIES else "refuse"
        self._tasks[task.id] = decision
        return decision

    def _allowed_url(self, tool, url):
        if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or parsed.port not in (None, 443)):
                return False
        except ValueError:
            return False
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        hosts = self.config.get(key, ())
        if isinstance(hosts, str):
            hosts = (hosts,)
        return parsed.hostname.lower() in {host.lower() for host in hosts}

    def check(self, call):
        if self._tasks.get(call.task_id) != "allow":
            return "deny"
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in TABLE_TOOLS:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if call.tool == "db.read":
                return "allow"
            if schema not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            return "allow" if self._allowed_url(call.tool, call.args.get("url")) else "deny"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
