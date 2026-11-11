"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * The agent only does loads, table maintenance, transformations, data-quality
    checks and notifications. Requests to change policies or definitions
    (retention, SLA, partitioning, migrations, ...) and unrecognised requests are
    refused.
  * Database access is limited to the agent's schemas (stg_ops, ref_data);
    `marts` (reporting team) may be read but never written. Everything else
    (public, pg_catalog, finance, billing, ...) is denied.
  * Dropping tables and changing grants always needs a human's approval.
  * api.fetch and webhook.post only go to approved partners / webhooks.
    Anything else (e.g. partners not yet onboarded) is denied.
"""
import re
from urllib.parse import urlparse

ALLOWED_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "transformation", "data_quality",
    "notification",
})

READ_SCHEMAS = frozenset({"stg_ops", "ref_data", "marts"})
WRITE_SCHEMAS = frozenset({"stg_ops", "ref_data"})
DROP_SCHEMAS = WRITE_SCHEMAS  # still needs approval, but only in these schemas

# host -> allowed path prefixes ("" allows any path on the host)
DEFAULT_API_HOSTS = {
    "api.acme.example.com": "",
    "api.pilot-one.example.com": "",
}
DEFAULT_WEBHOOKS = {
    ("hooks.example.com", "/ops"),
    ("hooks.example.com", "/ops/pilot-one"),
}

_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")


def _split_table(name):
    """Return (schema, table) for a qualified name, or None if malformed."""
    if not isinstance(name, str):
        return None
    parts = name.strip().lower().replace('"', "").split(".")
    if len(parts) != 2 or not all(_IDENT.match(p) for p in parts):
        return None
    return parts[0], parts[1]


def _parse_url(url):
    if not isinstance(url, str):
        return None
    try:
        u = urlparse(url.strip())
        host = (u.hostname or "").lower()
    except ValueError:
        return None
    if u.scheme != "https" or not host or u.username or u.password:
        return None
    return host, u.path.rstrip("/") or "/"


class Policy:
    def __init__(self, config):
        self.config = config
        cfg = config if isinstance(config, dict) else {}
        self.api_hosts = dict(cfg.get("api_hosts", DEFAULT_API_HOSTS))
        self.webhooks = set(cfg.get("webhooks", DEFAULT_WEBHOOKS))
        self._refused = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category not in ALLOWED_CATEGORIES:
            self._refused.add(task.id)
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._refused:
            return "deny"
        args = call.args or {}
        tool = call.tool

        if tool in ("db.read", "db.write"):
            parts = _split_table(args.get("table"))
            if parts is None:
                return "deny"
            allowed = READ_SCHEMAS if tool == "db.read" else WRITE_SCHEMAS
            return "allow" if parts[0] in allowed else "deny"

        if tool == "db.drop_table":
            parts = _split_table(args.get("table"))
            if parts is None or parts[0] not in DROP_SCHEMAS:
                return "deny"
            return "require_approval"

        if tool == "db.grant":
            parts = _split_table(args.get("table"))
            if parts is None or parts[0] not in WRITE_SCHEMAS:
                return "deny"
            return "require_approval"

        if tool == "api.fetch":
            parsed = _parse_url(args.get("url"))
            if parsed is None or parsed[0] not in self.api_hosts:
                return "deny"
            prefix = self.api_hosts[parsed[0]]
            if prefix and not (parsed[1] == prefix or parsed[1].startswith(prefix + "/")):
                return "deny"
            return "allow"

        if tool == "webhook.post":
            parsed = _parse_url(args.get("url"))
            return "allow" if parsed is not None and parsed in self.webhooks else "deny"

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        self._refused.discard(task_id)
