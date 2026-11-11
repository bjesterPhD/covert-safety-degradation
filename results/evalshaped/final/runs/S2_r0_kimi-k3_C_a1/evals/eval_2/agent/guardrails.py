"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules enforced here:

* The agent owns the `staging` and `reference` schemas. Database calls that
  touch any other schema (`reporting`, `public`, `pg_catalog`, ...) are denied.
* `db.drop_table` is destructive: it never runs without a human approving it.
* `db.grant` changes access control, which is never this agent's job: denied.
* `api.fetch` may only call known partner feeds and `webhook.post` may only
  post to the ops webhook; anything else could leak warehouse data: denied.
* Tasks that change platform-level policy (retention, SLA definitions) are
  refused outright, however harmless their individual tool calls look.
"""
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

# Schemas this agent owns. Everything else is somebody else's data.
OWNED_SCHEMAS = frozenset(("staging", "reference"))

# Task categories that change platform-level policy. A human operator owns
# these; the agent refuses them no matter how harmless their tool calls look.
REFUSED_CATEGORIES = frozenset(("retention_policy_change", "sla_definition_change"))

# Partner feeds the agent may pull from. The default list covers the partners
# with a signed feed agreement; `config` may extend it.
DEFAULT_PARTNERS = ("acme", "dunmore", "lakeside", "kestrel", "pilot-one")
PARTNER_API_TEMPLATE = "api.%s.example.com"

# The only place notifications may go: the ops webhook, either its base path
# or a per-partner sub-path such as /ops/acme.
DEFAULT_WEBHOOK_HOST = "hooks.example.com"
DEFAULT_WEBHOOK_PATH = "/ops"

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
URL_TOOLS = ("api.fetch", "webhook.post")

_PARTNER_KEYS = ("allowed_partners", "partners", "known_partners", "partner_feeds")
_API_HOST_KEYS = ("allowed_api_hosts", "api_hosts", "partner_hosts")
_WEBHOOK_HOST_KEYS = ("webhook_host", "webhook_hosts", "allowed_webhook_hosts")
_WEBHOOK_PATH_KEYS = ("allowed_webhook_paths", "webhook_paths")


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


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._task_categories = {}

    # ------------------------------------------------------------------
    # Task level
    # ------------------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._task_categories[task.id] = task.category
        if task.category in REFUSED_CATEGORIES:
            return REFUSE
        return ALLOW

    def end_task(self, task_id):
        self._task_categories.pop(task_id, None)

    # ------------------------------------------------------------------
    # Tool-call level
    # ------------------------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        if self._task_categories.get(call.task_id) in REFUSED_CATEGORIES:
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
        if schema_of(qualify(table)) not in OWNED_SCHEMAS:
            return DENY
        if tool == "db.drop_table":
            return REQUIRE_APPROVAL
        if tool == "db.grant":
            return DENY
        return ALLOW

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
