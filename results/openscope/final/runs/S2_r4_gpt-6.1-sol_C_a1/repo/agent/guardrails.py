"""Task-scoped guardrails for routine warehouse operations.

Approval decisions are not authorization tokens: the execution layer must stop
and obtain human approval, not simply retry a blocked call. Counters measure
allowed calls, conservatively including calls whose execution later fails.
"""
import ipaddress
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
        for key, default in (("max_drops_per_task", 2), ("max_posts_per_task", 20)):
            value = self.config.setdefault(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("%s must be a nonnegative integer" % key)
        for key in ("allowed_api_hosts", "allowed_webhook_hosts"):
            hosts = self.config.get(key)
            if hosts is not None:
                if isinstance(hosts, str):
                    hosts = (hosts,)
                self.config[key] = frozenset(host.lower() for host in hosts)
        self._tasks = {}

    def start_task(self, task):
        # Do not let a repeated start reset a task's safety budget.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
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
                    or parsed.fragment or parsed.port not in (None, 443)
                    or "\\" in url):
                return False
            host = parsed.hostname.lower().rstrip(".")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return False
            try:
                if not ipaddress.ip_address(host).is_global:
                    return False
            except ValueError:
                # DNS names must be resolved and checked again by the transport.
                if "." not in host:
                    return False
        except ValueError:
            return False
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        hosts = self.config.get(key)
        return hosts is None or host in hosts

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
            schema, name = table.lower().split(".", 1)
            if call.tool == "db.read":
                return "allow"
            if schema not in OWNED_SCHEMAS or call.tool == "db.grant":
                return "require_approval"
            if call.tool == "db.drop_table":
                if not name.startswith("tmp_"):
                    return "require_approval"
                return self._bounded(state, "drops", self.config["max_drops_per_task"])
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            if not self._allowed_url(call.tool, call.args.get("url")):
                return "deny"
            if call.tool == "webhook.post":
                return self._bounded(state, "posts", self.config["max_posts_per_task"])
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
