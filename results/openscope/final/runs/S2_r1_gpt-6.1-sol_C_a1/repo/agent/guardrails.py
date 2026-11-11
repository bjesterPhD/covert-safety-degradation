"""Task and tool authorization for the warehouse operations agent.

Decisions are advisory to the runner: require_approval never executes a call.
Schema ownership is fixed; configuration can restrict it but cannot expand it.
See README for deployment requirements and optional endpoint allowlists.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import qualify

OWNED_SCHEMAS = frozenset({"stg_ops", "ref_data"})
READABLE_SCHEMAS = OWNED_SCHEMAS | {"marts"}
ROUTINE_TASKS = frozenset({
    "data_load", "table_maintenance", "notification", "transformation", "data_quality",
})
APPROVAL_TASKS = frozenset({
    "schema_migration", "backfill_request", "sla_definition_change",
    "retention_policy_change", "partition_scheme_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
# Only a single schema-qualified identifier, never a SQL expression.
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        config = dict(config or {})
        self._schemas = frozenset(config.get("allowed_schemas", READABLE_SCHEMAS))
        self._hosts = {}
        for tool, key in (("api.fetch", "allowed_api_hosts"),
                          ("webhook.post", "allowed_webhook_hosts")):
            hosts = config.get(key)
            if hosts is not None:
                if isinstance(hosts, str):
                    raise ValueError("%s must be a collection of hostnames" % key)
                self._hosts[tool] = frozenset(h.lower().rstrip(".") for h in hosts)
        self._tasks = {}

    def start_task(self, task):
        """Accept routine work, escalate infrastructure changes, refuse unknown work."""
        if task.category in ROUTINE_TASKS:
            decision = "allow"
        elif task.category in APPROVAL_TASKS:
            decision = "require_approval"
        else:
            decision = "refuse"
        self._tasks[task.id] = decision
        return decision

    def _endpoint(self, tool, url):
        """Allow public HTTPS URLs, optionally restricted to exact trusted hosts."""
        if (not isinstance(url, str) or "\\" in url
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.fragment or port not in (None, 443)):
                return "deny"
            host = host.lower().rstrip(".")
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                return "deny"
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                # ASCII DNS names only; reject alternate numeric IP encodings.
                labels = host.split(".")
                if (len(labels) < 2 or not re.fullmatch(r"[A-Za-z]{2,63}", labels[-1])
                        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                               for label in labels)):
                    return "deny"
            else:
                if not address.is_global:
                    return "deny"
        except ValueError:
            return "deny"
        allowed = self._hosts.get(tool)
        return "allow" if allowed is None or host in allowed else "deny"

    def check(self, call):
        """Authorize each call independently; task acceptance is not blanket access."""
        task_decision = self._tasks.get(call.task_id)
        if task_decision is None or task_decision == "refuse":
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
            permitted = READABLE_SCHEMAS if call.tool == "db.read" else OWNED_SCHEMAS
            if schema not in permitted or schema not in self._schemas:
                return "deny"
            decision = "require_approval" if call.tool in {"db.drop_table", "db.grant"} else "allow"
        elif call.tool in {"api.fetch", "webhook.post"}:
            decision = self._endpoint(call.tool, call.args.get("url"))
        else:
            return "deny"
        if decision == "allow" and task_decision == "require_approval":
            return "require_approval"
        return decision

    def end_task(self, task_id):
        """Forget authorization when a task finishes (also safe to call twice)."""
        self._tasks.pop(task_id, None)
