"""Task-scoped authorization for warehouse operations."""
import re
from urllib.parse import urlsplit

from agent.paths import qualify, schema_of


class Policy:
    def __init__(self, config):
        self.config = config or {}
        unsupported = set(self.config) - {"api_urls", "webhook_urls", "network_budget"}
        if unsupported:
            raise ValueError("Unsupported policy configuration keys: %s" % sorted(unsupported))
        self._tasks = {}
        self._trusted = {
            "api.fetch": set(self.config.get("api_urls", [
                "https://api.acme.example.com/v1/orders",
            ])),
            "webhook.post": set(self.config.get("webhook_urls", [
                "https://hooks.example.com/ops",
            ])),
        }
        self._network_budget = self.config.get("network_budget", 3)

    def start_task(self, task):
        decision = "allow"
        if task.category in {"retention_policy_change", "sla_definition_change"}:
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "network": {}}
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*", table):
                return "deny"
            schema = schema_of(table).lower()
            if call.tool == "db.read":
                return "allow" if schema in {"etl_work", "ref_data", "analytics"} else "deny"
            if schema not in {"etl_work", "ref_data"}:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in self._trusted:
            url = call.args.get("url")
            if not isinstance(url, str):
                return "deny"
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not parsed.hostname
                        or parsed.username or parsed.password
                        or parsed.port not in (None, 443) or parsed.fragment):
                    return "deny"
            except ValueError:
                return "deny"
            if url in self._trusted[call.tool]:
                return "allow"
            used = state["network"].get(call.tool, 0)
            if used >= self._network_budget:
                return "require_approval"
            state["network"][call.tool] = used + 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
