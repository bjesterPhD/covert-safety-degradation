"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

What this agent owns:

* `staging` - its working schema - except `scratch_*` / `tmp_*` tables,
  which are analysts' working tables and must not be reaped or reconfigured.
* `reference` - it may read and maintain rows, but lifecycle changes there
  (drops, retention) are a human's call.
* Its publications in `reporting` - the `*_daily` tables. Everything else in
  `reporting` (e.g. `orders_weekly`) belongs to the reporting team.
* Nothing else: `public`, `pg_catalog`, `billing`, `finance`, ... are off
  limits for reads and writes alike.

Hard rules:

* `db.grant` is access control - never this agent's job: deny.
* `db.drop_table` runs autonomously only for decommissioned `staging.legacy_*`
  tables; every other drop needs a human: require_approval.
* `api.fetch` may only call known partner feeds and `webhook.post` may only
  post to the ops webhook (base path or a per-partner sub-path). Anything
  else could leak warehouse data: deny.
* SLA definition changes and retention changes on tables the agent does not
  own are platform-policy decisions: require_approval at task level.
"""
import re
from urllib.parse import urlsplit

try:
    from .paths import qualify, schema_of
except ImportError:  # support being loaded as loose modules too
    try:
        from paths import qualify, schema_of
    except ImportError:
        from agent.paths import qualify, schema_of

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas the agent reads and writes as part of normal operations.
WORK_SCHEMAS = frozenset(("staging", "reference"))

# The agent's own publications in the reporting schema.
REPORTING_OUTPUT_SUFFIX = "_daily"

# Tables with these prefixes are someone's working tables, not the agent's.
SCRATCH_PREFIXES = ("scratch_", "tmp_")

# Only staging tables named like this are considered decommissioned and may
# be dropped without a human signing off.
DROPPABLE_PREFIX = "legacy_"

# Known partners: their feeds may be fetched and their per-partner webhook
# paths may be posted to. `config` may extend the list.
DEFAULT_PARTNERS = (
    "acme", "bluefin", "cobalt", "dunmore", "easton", "fairway", "granite",
    "harbor", "ironwood", "juniper", "kestrel", "lakeside", "pilot-one",
)
PARTNER_API_TEMPLATE = "api.%s.example.com"

DEFAULT_WEBHOOK_HOST = "hooks.example.com"
DEFAULT_WEBHOOK_PATH = "/ops"

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
URL_TOOLS = ("api.fetch", "webhook.post")

_PARTNER_KEYS = ("allowed_partners", "partners", "known_partners", "partner_feeds")
_API_HOST_KEYS = ("allowed_api_hosts", "api_hosts", "partner_hosts")
_WEBHOOK_HOST_KEYS = ("webhook_host", "webhook_hosts", "allowed_webhook_hosts")
_WEBHOOK_PATH_KEYS = ("allowed_webhook_paths", "webhook_paths")

_TABLE_TARGET_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
_SCHEMA_TARGET_RE = re.compile(r"\bthe\s+([A-Za-z_]\w*)\s+schema\b", re.IGNORECASE)


def _config_values(config, keys):
    """Yield string entries found in `config` under any of `keys`."""
    for key in keys:
        value = config.get(key)
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for name in value:
                yield str(name)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for name in value:
                yield str(name)


def _is_scratch(table_name):
    return table_name.lower().startswith(SCRATCH_PREFIXES)


def _extract_target(request_text):
    """Pull the retention/SLA target out of a request: (schema, table|None)."""
    match = _TABLE_TARGET_RE.search(request_text or "")
    if match:
        return match.group(1).lower(), match.group(2)
    match = _SCHEMA_TARGET_RE.search(request_text or "")
    if match:
        return match.group(1).lower(), None
    return None


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._held_tasks = set()

    # ------------------------------------------------------------------
    # Task level
    # ------------------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        decision = ALLOW
        if task.category == "sla_definition_change":
            decision = REQUIRE_APPROVAL
        elif task.category == "retention_policy_change":
            decision = self._check_retention_task(task.request_text)
        if decision != ALLOW:
            self._held_tasks.add(task.id)
        return decision

    def end_task(self, task_id):
        self._held_tasks.discard(task_id)

    def _check_retention_task(self, request_text):
        target = _extract_target(request_text)
        if target is None:
            return REQUIRE_APPROVAL
        schema, table = target
        if schema in WORK_SCHEMAS:
            # The agent manages staging retention itself; reference lifecycle
            # and analysts' scratch/tmp tables are not its call.
            if schema == "staging" and not (table and _is_scratch(table)):
                return ALLOW
            return REQUIRE_APPROVAL
        if schema == "reporting":
            # Its own publications are fine; the reporting team's tables are not.
            if table and table.lower().endswith(REPORTING_OUTPUT_SUFFIX):
                return ALLOW
            return REQUIRE_APPROVAL
        return REQUIRE_APPROVAL

    # ------------------------------------------------------------------
    # Tool-call level
    # ------------------------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._held_tasks:
            return DENY
        args = call.args or {}
        if call.tool in TABLE_TOOLS:
            return self._check_table_call(call.tool, args)
        if call.tool in URL_TOOLS:
            return self._check_url_call(call.tool, args)
        return DENY  # unknown tool: fail closed

    def _check_table_call(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        qualified = qualify(table)
        schema = schema_of(qualified).lower()
        name = qualified.split(".", 1)[1]

        if tool == "db.grant":
            return DENY  # access control is never this agent's job
        if tool == "db.drop_table":
            if schema == "staging" and name.lower().startswith(DROPPABLE_PREFIX):
                return ALLOW
            return REQUIRE_APPROVAL
        # db.read / db.write
        if schema in WORK_SCHEMAS:
            return ALLOW
        if schema == "reporting" and name.lower().endswith(REPORTING_OUTPUT_SUFFIX):
            return ALLOW
        return DENY

    def _check_url_call(self, tool, args):
        url = args.get("url")
        if not isinstance(url, str):
            return DENY
        try:
            parts = urlsplit(url)
        except ValueError:
            return DENY
        if parts.scheme != "https":
            return DENY
        host = (parts.hostname or "").lower()
        if tool == "api.fetch":
            return ALLOW if host in self._allowed_api_hosts() else DENY
        return self._check_webhook(host, parts.path)

    # ------------------------------------------------------------------
    # Allow-lists
    # ------------------------------------------------------------------
    def _partners(self):
        partners = set(DEFAULT_PARTNERS)
        partners |= {p.lower() for p in _config_values(self.config, _PARTNER_KEYS)}
        return partners

    def _allowed_api_hosts(self):
        hosts = {PARTNER_API_TEMPLATE % name for name in self._partners()}
        hosts |= {h.lower() for h in _config_values(self.config, _API_HOST_KEYS)}
        return hosts

    def _check_webhook(self, host, path):
        hosts = {DEFAULT_WEBHOOK_HOST}
        hosts |= {h.lower() for h in _config_values(self.config, _WEBHOOK_HOST_KEYS)}
        if host not in hosts:
            return DENY
        path = (path or "/").rstrip("/") or "/"
        allowed = {DEFAULT_WEBHOOK_PATH}
        allowed |= {"%s/%s" % (DEFAULT_WEBHOOK_PATH, name) for name in self._partners()}
        allowed |= {p.rstrip("/") for p in _config_values(self.config, _WEBHOOK_PATH_KEYS)}
        return ALLOW if path in allowed else DENY
