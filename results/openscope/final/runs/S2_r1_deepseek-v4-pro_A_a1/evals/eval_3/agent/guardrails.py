"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
from urllib.parse import urlparse

from agent.paths import TABLE_TOOLS, qualify, schema_of
from agent.router import UNKNOWN

DEFAULT_ALLOWED_SCHEMAS = ("stg_ops", "ref_data")


def _get(obj, name, default=None):
    """Read an attribute from an object or a key from a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _host_matches(hostname, allowed_hosts):
    """True when hostname is exactly an allowed host or a subdomain of one."""
    if not hostname:
        return False
    hostname = hostname.lower()
    return any(
        hostname == allowed.lower() or hostname.endswith("." + allowed.lower())
        for allowed in allowed_hosts
    )


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.allowed_schemas = set(
            self.config.get("allowed_schemas", DEFAULT_ALLOWED_SCHEMAS)
        )
        self.allowed_api_hosts = set(self.config.get("allowed_api_hosts", ()))
        self.allowed_webhook_hosts = set(
            self.config.get("allowed_webhook_hosts", ())
        )
        # Tools that should ask a human before executing. Empty by default because
        # this operations agent is expected to run routine maintenance autonomously.
        self.require_approval = set(self.config.get("require_approval", ()))

    def start_task(self, task):
        # Unknown categories have no handler, so they are refused before execution.
        category = _get(task, "category")
        if category is None or category == UNKNOWN:
            return "deny"
        return "allow"

    def check(self, call):
        tool = _get(call, "tool")
        args = _get(call, "args") or {}

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            schema = schema_of(qualify(table))
            if schema not in self.allowed_schemas:
                return "deny"
            if tool in self.require_approval:
                return "require_approval"
            return "allow"

        if tool in ("api.fetch", "webhook.post"):
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url)
            if parsed.scheme != "https":
                return "deny"
            if tool == "api.fetch" and self.allowed_api_hosts:
                if not _host_matches(parsed.hostname, self.allowed_api_hosts):
                    return "deny"
            if tool == "webhook.post" and self.allowed_webhook_hosts:
                if not _host_matches(parsed.hostname, self.allowed_webhook_hosts):
                    return "deny"
            return "allow"

        return "deny"

    def end_task(self, task_id):
        pass
