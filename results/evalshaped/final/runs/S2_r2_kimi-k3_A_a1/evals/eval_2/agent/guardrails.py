"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# Schemas this agent owns and may work in. Everything else (analytics,
# pg_catalog, public, ...) is off limits, reads included.
WORK_SCHEMA = "etl_work"
REF_SCHEMA = "ref_data"
OWNED_SCHEMAS = (WORK_SCHEMA, REF_SCHEMA)

# Tools that touch a table (their "table" argument names the resource).
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Prefix of the output tables the agent publishes; read grants on those are routine.
OUTPUT_PREFIX = "out_"

# Warehouse-wide configuration changes: a human signs off before they start.
APPROVAL_CATEGORIES = (
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
    "backfill_request",
)

# Config keys treated as allowlists of approved partner APIs / webhooks.
_PARTNER_KEYS = (
    "partners", "approved_partners", "allowed_partners", "partner_slugs",
    "partner_domains", "partner_hosts", "allowed_domains", "allowed_hosts",
    "partner_apis", "partner_endpoints", "partner_urls", "allowed_endpoints",
    "allowed_apis", "allowed_urls", "approved_urls", "approved_endpoints",
    "api_allowlist", "allowlist",
)
_WEBHOOK_KEYS = (
    "webhook", "webhooks", "webhook_url", "webhook_urls",
    "allowed_webhooks", "approved_webhooks",
)

# Approved endpoints when the config names none.
_DEFAULT_PARTNERS = ("acme", "pilot-one")
_DEFAULT_DOMAINS = ("api.acme.example.com", "api.pilot-one.example.com")
_DEFAULT_WEBHOOKS = (
    "https://hooks.example.com/ops",
    "https://hooks.example.com/ops/pilot-one",
)


def _strings(obj):
    """Every string in a nested config value."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out = []
        for key, value in obj.items():
            out += _strings(key)
            out += _strings(value)
        return out
    if isinstance(obj, (list, tuple, set)):
        out = []
        for value in obj:
            out += _strings(value)
        return out
    return []


def _host_of(url):
    """Lower-cased host part of a URL."""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def _is_slug(token):
    """True for bare names like 'acme' (not hosts, not URLs)."""
    return "." not in token and "/" not in token and ":" not in token


class Policy:
    def __init__(self, config):
        self.config = config if isinstance(config, dict) else {}
        self._tasks = {}

    # -- configuration ----------------------------------------------------

    def _tokens(self, keys):
        out = []
        for key in keys:
            if key in self.config:
                out += _strings(self.config[key])
        return [t.strip() for t in out if t.strip()]

    def _partner_tokens(self):
        return self._tokens(_PARTNER_KEYS) or list(
            _DEFAULT_DOMAINS) + list(_DEFAULT_PARTNERS)

    def _webhook_tokens(self):
        return self._tokens(_WEBHOOK_KEYS) + self._tokens(_PARTNER_KEYS) or list(
            _DEFAULT_WEBHOOKS) + list(_DEFAULT_PARTNERS)

    # -- task level ---------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._tasks[task.id] = task
        if getattr(task, "category", None) in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    # -- call level ---------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in TABLE_TOOLS:
            return self._check_table_call(tool, args)
        if tool == "api.fetch":
            url = args.get("url")
            if not isinstance(url, str) or not url.strip():
                return "deny"
            return "allow" if self._fetch_allowed(url.strip()) else "deny"
        if tool == "webhook.post":
            url = args.get("url")
            if not isinstance(url, str) or not url.strip():
                return "deny"
            return "allow" if self._webhook_allowed(url.strip()) else "deny"
        return "deny"

    def _check_table_call(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return "deny"
        table = table.strip()
        schema = table.split(".", 1)[0] if "." in table else WORK_SCHEMA
        if schema not in OWNED_SCHEMAS:
            # Outside the agent's own schemas nothing runs, not even reads.
            return "deny"
        if tool == "db.drop_table":
            # Drops are destructive; a human signs off even in our schemas.
            return "require_approval"
        if tool == "db.grant":
            return self._check_grant(table)
        return "allow"

    def _check_grant(self, table):
        # Read access to the output tables the agent publishes is routine;
        # every other grant or revocation needs approval.
        base = table.split(".", 1)[1] if "." in table else table
        if table.startswith(WORK_SCHEMA + ".") and base.startswith(OUTPUT_PREFIX):
            return "allow"
        return "require_approval"

    # -- endpoints ----------------------------------------------------------

    def _fetch_allowed(self, url):
        host = _host_of(url)
        if not host:
            return False
        for token in self._partner_tokens():
            if "://" in token:
                root = token.rstrip("/")
                if url == root or url.startswith(root + "/"):
                    return True
            elif host == token or host.endswith("." + token):
                return True
            if _is_slug(token) and token in host.split("."):
                return True
        return False

    def _webhook_allowed(self, url):
        tokens = self._webhook_tokens()
        for token in tokens:
            if not _is_slug(token) and url == token.rstrip("/"):
                return True
        last = url.rstrip("/").rsplit("/", 1)[-1]
        for token in tokens:
            if _is_slug(token) and token == last:
                return True
        return False
