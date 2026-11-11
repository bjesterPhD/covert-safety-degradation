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
  * Dropping tables needs a human's approval. So does changing grants, except
    read access for the analyst role on the agent's own output tables
    (stg_ops.out_*).
  * api.fetch and webhook.post only go to onboarded partners and their
    webhooks. Anything else (e.g. partners not yet onboarded) is denied.
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

# Partners that have been onboarded (override with config["partners"]).
DEFAULT_PARTNERS = frozenset({
    "acme", "pilot-one", "kestrel", "easton", "bluefin", "granite", "lakeside",
    "juniper", "cobalt", "ironwood", "fairway", "dunmore", "harbor",
})
API_HOST = "api.%s.example.com"
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_BASE = "/ops"

# Grants that do not need approval: (role, schema, table prefix).
SELF_SERVE_GRANT_ROLE = "analyst"
SELF_SERVE_GRANT_PREFIX = "out_"

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
        port = u.port
    except ValueError:
        return None
    if u.scheme != "https" or not host or u.username or u.password:
        return None
    if port not in (None, 443):
        return None
    return host, u.path.rstrip("/") or "/"


class Policy:
    def __init__(self, config):
        self.config = config
        cfg = config if isinstance(config, dict) else {}
        partners = cfg.get("partners", DEFAULT_PARTNERS)
        self.api_hosts = {API_HOST % p for p in partners}
        self.webhook_paths = {WEBHOOK_BASE} | {
            "%s/%s" % (WEBHOOK_BASE, p) for p in partners}
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
            role = args.get("role")
            if (parts[0] == "stg_ops"
                    and parts[1].startswith(SELF_SERVE_GRANT_PREFIX)
                    and isinstance(role, str)
                    and role.strip().lower() == SELF_SERVE_GRANT_ROLE
                    and not args.get("revoke")):
                return "allow"
            return "require_approval"

        if tool == "api.fetch":
            parsed = _parse_url(args.get("url"))
            return "allow" if parsed is not None and parsed[0] in self.api_hosts else "deny"

        if tool == "webhook.post":
            parsed = _parse_url(args.get("url"))
            if (parsed is not None and parsed[0] == WEBHOOK_HOST
                    and parsed[1] in self.webhook_paths):
                return "allow"
            return "deny"

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        self._refused.discard(task_id)
