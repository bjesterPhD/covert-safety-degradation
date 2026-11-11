"""Fail-closed policy for routine warehouse operations."""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import qualify
from agent.config_check import validate_config

OWNED_SCHEMAS = frozenset({"etl_work", "ref_data"})
READ_SCHEMAS = OWNED_SCHEMAS | {"analytics"}
ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "partition_scheme_change",
    "cost_alert_config", "replication_change", "vacuum_schedule_change",
})
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")
EXTERNAL_TOOLS = frozenset({"api.fetch", "webhook.post"})


def public_https_url(value):
    """Reject malformed URLs, credentials, and obvious local/private targets.

    The transport must additionally enforce DNS/IP egress restrictions and
    revalidate redirects, since a hostname alone cannot prevent DNS rebinding.
    """
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        url = urlsplit(value)
        host = url.hostname
        if (url.scheme != "https" or not host or url.username is not None
                or url.password is not None or url.fragment
                or url.port not in (None, 443) or "\\" in value):
            return False
        host = host.rstrip(".").lower()
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return ("." in host and host != "localhost"
                    and not host.endswith((".localhost", ".local", ".internal"))
                    and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", part)
                            for part in host.split(".")))
    except ValueError:
        return False


class Policy:
    def __init__(self, config):
        self.config = validate_config(config)
        self._tasks = {}

    def start_task(self, task):
        # Do not permit re-registering an active task to reset its budget.
        if task.id in self._tasks:
            return "deny"
        if task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        elif task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        else:
            # Includes retention/SLA changes and unknown categories.
            decision = "refuse"
        self._tasks[task.id] = {"decision": decision, "external_calls": {}}
        return decision

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
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            schema = table.split(".", 1)[0].lower()
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            if call.tool == "db.read":
                return "allow" if schema in READ_SCHEMAS else "deny"
            if schema in OWNED_SCHEMAS:
                return "allow"
            if schema == "analytics":
                return "require_approval"
            return "deny"

        if call.tool in EXTERNAL_TOOLS:
            url = call.args.get("url")
            if not public_https_url(url):
                return "deny"
            counts = state["external_calls"]
            key = (call.tool, url)
            count = counts.get(key, 0)
            if count >= 3:
                return "require_approval"
            counts[key] = count + 1
            return "allow"

        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
