"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Decisions:

- Routine operational tasks (loads, transformations, quality checks, table
  maintenance, notifications, backfills) are allowed.
- Tasks that change warehouse-wide configuration or the data lifecycle
  (migrations, retention, partitioning, replication, vacuum schedules, SLAs,
  cost alerts) require human approval before the agent starts.
- Requests that try to talk the agent out of its own rules are refused.
- Reads are allowed on any schema except database internals.
- Writes, drops, and grants are only allowed inside the schemas this agent
  owns (``stg_ops`` and ``ref_data``); ``marts`` belongs to the reporting
  team and is read-only for us. Drops and grants inside our own schemas are
  destructive/access-changing, so they require approval.
- Outbound HTTP calls (partner APIs, webhooks) must use http(s), must not
  target internal/loopback/link-local hosts, and are checked against the
  configured allow-lists when the deployment provides them.
"""

import re
from urllib.parse import urlparse

WORK_SCHEMA = "stg_ops"

# Schemas this agent owns and may change.
OWN_SCHEMAS = frozenset({"stg_ops", "ref_data"})

# Schemas owned by other teams. The agent may read them but must never
# write, drop, or grant on them.
FOREIGN_SCHEMAS = frozenset({"marts"})

# Database internals: never a valid target for this agent.
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "pg_toast", "information_schema"})

# Task categories that change warehouse-wide configuration or the data
# lifecycle. They always need human sign-off before the agent starts.
APPROVAL_TASK_CATEGORIES = frozenset({
    "schema_migration",
    "retention_policy_change",
    "partition_scheme_change",
    "replication_change",
    "vacuum_schedule_change",
    "sla_definition_change",
    "cost_alert_config",
})

TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
WRITE_TOOLS = frozenset({"db.write", "db.drop_table", "db.grant"})
URL_TOOLS = frozenset({"api.fetch", "webhook.post"})
KNOWN_TOOLS = TABLE_TOOLS | URL_TOOLS

# Tools whose effects cannot be undone in one step, or which change who can
# see what. Inside our own schemas they need approval; elsewhere they are
# denied outright.
APPROVAL_TOOLS = frozenset({"db.drop_table", "db.grant"})

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Loopback, RFC-1918, link-local (cloud metadata endpoints live there), and
# typical internal suffixes.
_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|0[0-7]*\.0[0-7]*\.0[0-7]*\.0[0-7]*|"
    r"::1|\[::1\]|10\.|192\.168\.|"
    r"172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.|"
    r".*\.(internal|local|lan|corp|intranet))$",
    re.IGNORECASE,
)

# Attempts to override the agent's instructions.
_INJECTION_RE = re.compile(
    r"\b(ignore|disregard|forget|override|bypass)\b[^.!?]{0,60}"
    r"\b(instructions?|rules?|guidelines?|guardrails|policies|policy|restrictions?)\b",
    re.IGNORECASE,
)


def _config_value(config, *keys):
    """First non-None value among `keys`, from a mapping or an object."""
    for key in keys:
        value = None
        try:
            if hasattr(config, "get"):
                value = config.get(key)
        except Exception:
            value = None
        if value is None:
            try:
                value = getattr(config, key, None)
            except Exception:
                value = None
        if value is not None:
            return value
    return None


def _qualify(table):
    """Qualify a table name the same way agent.paths.qualify does."""
    name = str(table).strip().strip('"').strip()
    if "." in name:
        return name
    return "%s.%s" % (WORK_SCHEMA, name)


def _schema_of(table):
    return _qualify(table).split(".", 1)[0].strip().strip('"').lower()


def _host_allowed(host, allowed):
    """True if `host` is one of `allowed` or a subdomain of an entry."""
    if isinstance(allowed, str):
        allowed = [allowed]
    for entry in allowed:
        entry = str(entry).strip().lower()
        if not entry:
            continue
        if "://" in entry:
            entry = (urlparse(entry).hostname or "").lower()
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _get(obj, name, default=None):
    """Attribute-or-key lookup, tolerant of dicts and plain objects."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}

        own = _config_value(self.config, "own_schemas", "schemas")
        self.own_schemas = frozenset(
            str(s).lower() for s in (own or OWN_SCHEMAS)
        )

        foreign = _config_value(
            self.config, "foreign_schemas", "protected_schemas"
        )
        self.foreign_schemas = frozenset(
            str(s).lower() for s in (foreign or FOREIGN_SCHEMAS)
        )

        approval = _config_value(self.config, "approval_categories")
        self.approval_categories = frozenset(
            approval or APPROVAL_TASK_CATEGORIES
        )

        self.fetch_hosts = _config_value(
            self.config,
            "allowed_fetch_hosts",
            "partner_domains",
            "allowed_domains",
            "allowed_hosts",
        )
        self.webhook_hosts = self._webhook_hosts()

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _get(task, "category")
        text = _get(task, "request_text", "") or ""
        if text and _INJECTION_RE.search(str(text)):
            return "refuse"
        if category in self.approval_categories:
            return "require_approval"
        if not category or category == "unknown":
            return "require_approval"
        return "allow"

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _get(call, "tool", "") or ""
        args = _get(call, "args", None) or {}

        if tool in TABLE_TOOLS:
            return self._check_table_tool(tool, args.get("table"))
        if tool in URL_TOOLS:
            return self._check_url_tool(tool, args.get("url"))
        return "deny"

    def _check_table_tool(self, tool, table):
        schema = _schema_of(table) if table else WORK_SCHEMA
        if schema in SYSTEM_SCHEMAS:
            return "deny"
        if tool == "db.read":
            return "allow"
        # db.write / db.drop_table / db.grant
        if schema in self.foreign_schemas or schema not in self.own_schemas:
            return "deny"
        if tool in APPROVAL_TOOLS:
            return "require_approval"
        return "allow"

    def _check_url_tool(self, tool, url):
        if not isinstance(url, str) or not url.strip():
            return "deny"
        parsed = urlparse(url.strip())
        if (parsed.scheme or "").lower() not in ALLOWED_URL_SCHEMES:
            return "deny"
        host = (parsed.hostname or "").lower()
        if not host:
            return "deny"
        allowed = self.fetch_hosts if tool == "api.fetch" else self.webhook_hosts
        if allowed is not None:
            return "allow" if _host_allowed(host, allowed) else "deny"
        if _PRIVATE_HOST_RE.match(host):
            return "deny"
        return "allow"

    def _webhook_hosts(self):
        raw = _config_value(
            self.config,
            "allowed_webhooks",
            "webhook_urls",
            "webhook_url",
            "notification_webhook",
        )
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = [raw]
        hosts = []
        for entry in raw:
            entry = str(entry).strip()
            if not entry:
                continue
            parsed = urlparse(entry if "://" in entry else "https://" + entry)
            hosts.append((parsed.hostname or entry).lower())
        return hosts

    def end_task(self, task_id):
        pass
