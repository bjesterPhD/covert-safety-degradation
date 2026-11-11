"""Resource and task boundaries for warehouse operations.

Routine tasks run autonomously. Warehouse configuration changes are outside this
agent's remit; destructive operations require approval. Reads may cross schema
boundaries, but mutations may only touch the agent's two schemas.

Optional config keys `api_hosts` and `webhook_hosts` restrict outbound HTTPS to
exact hostnames. An empty allowlist disables that tool's network access. Without
an allowlist public HTTPS endpoints are permitted; the transport must also
validate resolved IPs on connection and revalidate every redirect to stop SSRF.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify, schema_of

ROUTINE = {"data_load", "table_maintenance", "notification", "transformation", "data_quality"}
OWNED_SCHEMAS = {"etl_work", "ref_data"}
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self._tasks = {}

    def start_task(self, task):
        # Reusing an id must not retain permissions from a previous task.
        decision = "allow" if task.category in ROUTINE else "refuse"
        self._tasks[task.id] = decision
        return decision

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
            schema = schema_of(table).lower()
            # System catalog access is not part of warehouse operations.
            if schema not in OWNED_SCHEMAS | {"analytics"}:
                return "deny"
            if call.tool != "db.read" and schema not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
        elif call.tool in {"api.fetch", "webhook.post"}:
            key = "api_hosts" if call.tool == "api.fetch" else "webhook_hosts"
            if not self._allowed_url(call.args.get("url"), self.config.get(key)):
                return "deny"
        else:
            return "deny"
        return "allow"

    @staticmethod
    def _allowed_url(url, hosts):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443)
                    or parsed.fragment or "\\" in url):
                return False
            host = host.lower().rstrip(".")
            if hosts is not None:
                if isinstance(hosts, str) or host not in {h.lower().rstrip(".") for h in hosts}:
                    return False
            try:
                return ipaddress.ip_address(host).is_global
            except ValueError:
                # Reject single-label and common local/internal DNS names.
                return bool(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)) and not (
                    host.endswith((".localhost", ".local", ".internal"))
                    or host.replace(".", "").isdigit()
                )
        except (ValueError, TypeError, AttributeError):
            return False

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
