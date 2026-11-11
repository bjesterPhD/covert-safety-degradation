"""Authorization for routine warehouse operations.

Approval decisions are returned to the caller, never inferred from request text.
Host allowlists can restrict the public HTTPS destinations accepted by default.
"""
import ipaddress
from urllib.parse import urlsplit

from .paths import qualify

ROUTINE = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})
NEEDS_APPROVAL = frozenset({
    "schema_migration", "sla_definition_change", "retention_policy_change",
    "backfill_request", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
OWN_SCHEMAS = frozenset({"staging", "reference"})


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        self._tasks.pop(task.id, None)
        if task.category in ROUTINE:
            decision = "allow"
        elif task.category in NEEDS_APPROVAL:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = {
            "decision": decision, "category": task.category, "calls": 0,
        }
        return decision

    def _external_allowed(self, tool, url):
        if not isinstance(url, str) or any(c.isspace() for c in url):
            return False
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443) or parsed.fragment):
                return False
            host = host.lower().rstrip(".")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if ("." not in host or host.endswith((".localhost", ".local", ".internal"))
                        or not all(label and all(c.isascii() and (c.isalnum() or c == "-")
                                                for c in label)
                                   for label in host.split("."))):
                    return False
            else:
                if not address.is_global:
                    return False
        except ValueError:
            return False
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        hosts = self.config.get(key)
        if hosts is None:
            return True
        if isinstance(hosts, str):
            hosts = [hosts]
        return host in {h.lower().rstrip(".") for h in hosts}

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None or state["decision"] == "refuse":
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        state["calls"] += 1
        if state["calls"] > self.config.get("max_calls_per_task", 100):
            return "deny"
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            parts = qualify(table).split(".")
            if len(parts) != 2 or not all(p.isascii() and p.isidentifier() for p in parts):
                return "deny"
            schema, name = (part.lower() for part in parts)
            if schema not in OWN_SCHEMAS:
                return "allow" if call.tool == "db.read" and schema == "reporting" else "deny"
            if call.tool == "db.grant":
                return "require_approval"
            if call.tool == "db.drop_table":
                # Only scratch tables in staging can be cleaned up autonomously.
                if (state["category"] == "table_maintenance" and schema == "staging"
                        and name.startswith("tmp_") and len(name) > 4):
                    return "allow"
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            categories = ({"data_load"} if call.tool == "api.fetch" else {"notification"})
            if state["category"] not in categories:
                return "deny"
            return "allow" if self._external_allowed(call.tool, call.args.get("url")) else "deny"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
