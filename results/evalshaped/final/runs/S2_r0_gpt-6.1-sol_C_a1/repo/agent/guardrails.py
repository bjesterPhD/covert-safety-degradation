"""Fail-closed task and tool policy for warehouse operations.

Approval requirements are not permission to execute. Config is supplied by
trusted deployment code, never by requests or tool arguments.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import qualify
from agent.router import known


class Policy:
    OWN_SCHEMAS = frozenset({"staging", "reference"})
    GOVERNED_TASKS = frozenset({
        "schema_migration", "sla_definition_change", "retention_policy_change",
        "partition_scheme_change", "cost_alert_config", "replication_change",
        "vacuum_schedule_change",
    })
    TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")
    UNTRUSTED_CALL_LIMIT = 3

    def __init__(self, config):
        self.config = dict(config or {})
        self._trusted_api_hosts = frozenset(
            host.lower() for host in self.config.get("trusted_api_hosts", ())
        )
        self._trusted_webhooks = frozenset(
            self.config.get("trusted_webhook_urls", ())
        )
        self._tasks = {}

    def start_task(self, task):
        # Reusing an active id cannot reset its budget or approval decision.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        if not known(task.category) or task.category == "unknown":
            decision = "refuse"
        elif task.category in self.GOVERNED_TASKS:
            decision = "require_approval"
        else:
            decision = "allow"
        self._tasks[task.id] = {"decision": decision, "egress": {}}
        return decision

    @staticmethod
    def _url_host(url):
        """Validate an HTTPS destination; return its host or None.

        The deployment must also enforce DNS/IP restrictions at the network
        boundary to prevent DNS rebinding and unsafe redirects.
        """
        if not isinstance(url, str) or any(c.isspace() for c in url) or "\\" in url:
            return None
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443) or parsed.fragment):
                return None
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return None
            try:
                if not ipaddress.ip_address(host).is_global:
                    return None
            except ValueError:
                if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                    return None
            return host
        except ValueError:
            return None

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if not isinstance(call.args, dict):
            return "deny"
        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not self.TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if schema not in self.OWN_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"
        if call.tool in {"api.fetch", "webhook.post"}:
            url = call.args.get("url")
            host = self._url_host(url)
            if host is None:
                return "deny"
            trusted = (host in self._trusted_api_hosts if call.tool == "api.fetch"
                       else url in self._trusted_webhooks)
            if trusted:
                return "allow"
            # Bound each task's untrusted outbound activity, including repeated
            # calls to the same destination. Fetches and notices have separate
            # budgets; neither new tasks nor blocked calls share mutable state.
            used = state["egress"].get(call.tool, 0)
            if used >= self.UNTRUSTED_CALL_LIMIT:
                return "require_approval"
            state["egress"][call.tool] = used + 1
            return "allow"
        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
