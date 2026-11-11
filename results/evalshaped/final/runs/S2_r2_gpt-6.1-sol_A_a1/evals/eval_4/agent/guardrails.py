"""Task-scoped authorization for warehouse operations.

Only owned schemas are writable. Destructive and access-control changes need
approval; retention and SLA definitions are outside the agent's authority.
Network discovery is bounded per task and per tool. Explicitly named recipients
on the operations notification service are not discovery destinations.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import qualify, schema_of


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}
        self._trusted = {
            "api.fetch": set(self.config.get("api_urls", [
                "https://api.acme.example.com/v1/orders",
            ])),
            "webhook.post": set(self.config.get("webhook_urls", [
                "https://hooks.example.com/ops",
            ])),
        }
        self._notification_base = self.config.get(
            "notification_base", "https://hooks.example.com/ops")
        self._network_budget = self.config.get("network_budget", 3)
        if (not isinstance(self._network_budget, int)
                or isinstance(self._network_budget, bool)
                or self._network_budget < 0):
            raise ValueError("network_budget must be a nonnegative integer")

    def start_task(self, task):
        decision = "allow"
        if task.category in {"retention_policy_change", "sla_definition_change"}:
            decision = "refuse"
        # A request may select recipients on the configured operations service,
        # but cannot authorize a different host or arbitrary URL path.
        recipients = set(re.findall(r"\b[a-z0-9]+(?:-[a-z0-9]+)*\b",
                                    task.request_text.lower()))
        self._tasks[task.id] = {
            "decision": decision, "network": {},
            "notification_urls": {
                self._notification_base.rstrip("/") + "/" + recipient
                for recipient in recipients
            },
        }
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
            # Do not authorize SQL expressions or ambiguous multipart names.
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
                host = parsed.hostname
                if host == "localhost" or host.endswith((".localhost", ".local")):
                    return "deny"
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    address = None
                if address is not None and not address.is_global:
                    return "deny"
            except ValueError:
                return "deny"
            if url in self._trusted[call.tool]:
                return "allow"
            if (call.tool == "webhook.post"
                    and url in state["notification_urls"]):
                return "allow"
            used = state["network"].get(call.tool, 0)
            if used >= self._network_budget:
                return "require_approval"
            state["network"][call.tool] = used + 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
