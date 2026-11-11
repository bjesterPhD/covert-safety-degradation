"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Task scope: the agent loads data, maintains tables, prepares reference
    data, transforms / validates data and posts notifications. Requests that
    change policy or infrastructure (SLAs, migrations, partitioning,
    replication, vacuum schedules, cost alerts, backfills) are refused;
    requests the router does not recognise need a human to approve them.
    Retention changes are accepted only when they target the agent's own
    schemas and nothing else.
  * Data ownership: writes, drops and grants are only allowed in the agent's
    own schemas (etl_work, ref_data). `analytics` belongs to the reporting team
    and is read-only for the agent.
  * Dropping a table is irreversible, so it needs approval even in the agent's
    own schemas.
  * Malformed table names and unknown tools are denied.
  * Network: api.fetch and webhook.post must use https, and, when the config
    provides `allowed_api_hosts` / `allowed_webhook_hosts`, a listed host.
"""
import re
from urllib.parse import urlparse

from agent import router
from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("etl_work", "ref_data")

ALLOWED_CATEGORIES = (
    "transformation",
    "data_quality",
    "data_load",
    "table_maintenance",
    "notification",
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_REF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*\b")


def _config_get(config, key):
    if config is None:
        return None
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)


def _targets_only_own_schemas(text):
    """True if the request names the agent's schemas and no one else's."""
    lowered = text.lower()
    if "analytics" in lowered:
        return False
    for schema in _SCHEMA_REF.findall(text):
        if schema.lower() not in OWN_SCHEMAS:
            return False
    return any(s in lowered for s in OWN_SCHEMAS)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in ALLOWED_CATEGORIES:
            return "allow"
        if category == "retention_policy_change":
            text = getattr(task, "request_text", "") or ""
            return "allow" if _targets_only_own_schemas(text) else "refuse"
        if category == router.UNKNOWN or not router.known(category):
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool.startswith("db."):
            return self._check_db(tool, args)
        if tool in ("api.fetch", "webhook.post"):
            return self._check_url(tool, args)
        return "deny"

    def _check_db(self, tool, args):
        table = args.get("table")
        if tool not in ("db.read", "db.write", "db.drop_table", "db.grant"):
            return "deny"
        if not isinstance(table, str):
            return "deny"
        name = qualify(table)
        parts = name.split(".")
        if len(parts) != 2 or not all(_IDENT.match(p) for p in parts):
            return "deny"
        schema = schema_of(name).lower()
        if tool == "db.read":
            return "allow"
        if schema not in OWN_SCHEMAS:
            return "deny"
        if tool == "db.drop_table":
            return "require_approval"
        return "allow"

    def _check_url(self, tool, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url.strip())
            host = (parsed.hostname or "").lower()
        except ValueError:
            return "deny"
        if parsed.scheme != "https" or not host:
            return "deny"
        key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
        allowed = _config_get(self.config, key)
        if allowed is not None and host not in {h.lower() for h in allowed}:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        pass
