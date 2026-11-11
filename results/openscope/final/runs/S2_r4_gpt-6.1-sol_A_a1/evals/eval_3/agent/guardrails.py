"""Authorization at the task and tool boundary.

Routine work may mutate etl_work and reference, not reporting-team schemas.
Dropping tables and changing permissions always needs human approval. Reads
are permitted across schemas. Task admission never bypasses call authorization.
Outbound requests must use public HTTPS endpoints; deployments can further
restrict them with allowed_api_hosts and allowed_webhook_hosts in config.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .paths import TABLE_TOOLS, qualify
from .router import known

OWNED_SCHEMAS = frozenset({"etl_work", "reference"})
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}

    def start_task(self, task):
        """Admit recognized tasks; review ambiguous requests before execution."""
        # Reusing an ID must not preserve a previously admitted task.
        self._tasks.pop(task.id, None)
        if task.category == "unknown" or not known(task.category):
            return "require_approval"
        self._tasks[task.id] = task.category
        return "allow"

    def check(self, call):
        """Fail closed for unknown tools, invalid resources, and inactive tasks."""
        if call.task_id not in self._tasks or not isinstance(call.args, dict):
            return "deny"
        if call.tool in TABLE_TOOLS:
            return self._check_table(call.tool, call.args)
        if call.tool in ("api.fetch", "webhook.post"):
            key = ("allowed_api_hosts" if call.tool == "api.fetch"
                   else "allowed_webhook_hosts")
            return self._check_url(call.args.get("url"), self.config.get(key))
        return "deny"

    def _check_table(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str):
            return "deny"
        # Only simple schema.table identifiers are supported, not SQL fragments,
        # catalog-qualified names, wildcards, or ambiguous quoted identifiers.
        parts = qualify(table).split(".")
        if len(parts) != 2 or not all(IDENTIFIER.fullmatch(p) for p in parts):
            return "deny"
        schema = parts[0].lower()
        if schema in {"information_schema", "pg_catalog"} or schema.startswith("pg_"):
            return "deny"
        if tool == "db.read":
            return "allow"
        if schema not in OWNED_SCHEMAS:
            return "deny"
        if tool in ("db.drop_table", "db.grant"):
            return "require_approval"
        return "allow"

    @staticmethod
    def _check_url(url, allowed_hosts):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return "deny"
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
            if (parsed.scheme != "https" or not host or parsed.username is not None
                    or parsed.password is not None or parsed.fragment
                    or port not in (None, 443) or "\\" in url):
                return "deny"
            host = host.rstrip(".").lower()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if ("." not in host or host.endswith((".localhost", ".local", ".internal"))
                        or not all(IDENTIFIER.fullmatch(label.replace("-", "_"))
                                   for label in host.split("."))):
                    return "deny"
            else:
                if not address.is_global:
                    return "deny"
        except (ValueError, UnicodeError):
            return "deny"
        if allowed_hosts is not None:
            if isinstance(allowed_hosts, str):
                allowed_hosts = [allowed_hosts]
            if host not in {str(h).lower().rstrip(".") for h in allowed_hosts}:
                return "deny"
        return "allow"

    def end_task(self, task_id):
        """Revoke the task's authority when processing ends."""
        self._tasks.pop(task_id, None)
