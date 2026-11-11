"""Task-scoped guardrails for warehouse operations.

Routine work in owned schemas is autonomous. Cross-team mutations, access
changes and warehouse policy changes need approval. Unknown work is refused.
Only HTTPS network calls are supported; deployments can narrow destinations
with allowed_api_hosts and allowed_webhook_hosts. Per-task limits bound bulk
notifications and destructive maintenance.
"""
import re
from urllib.parse import urlsplit

from agent.paths import qualify
from agent.router import known


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
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category != "unknown" and known(task.category):
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "drops": 0, "posts": 0}
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
        hosts = self.config.get(key)
        if hosts is None:
            return True
        if isinstance(hosts, str):
            hosts = (hosts,)
        return parsed.hostname.lower() in {host.lower() for host in hosts}

    def _bounded(self, state, counter, limit):
        if state[counter] >= limit:
            return "require_approval"
        state[counter] += 1
        return "allow"

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None or state["decision"] != "allow":
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
            if schema not in OWNED_SCHEMAS or call.tool == "db.grant":
                return "require_approval"
            if call.tool == "db.drop_table":
                return self._bounded(state, "drops", self.config.get("max_drops_per_task", 2))
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            if not self._allowed_url(call.tool, call.args.get("url")):
                return "deny"
            if call.tool == "webhook.post":
                return self._bounded(state, "posts", self.config.get("max_posts_per_task", 5))
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
