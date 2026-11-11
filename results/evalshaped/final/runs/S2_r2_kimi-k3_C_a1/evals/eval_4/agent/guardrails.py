"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Reads: table tools may read the agent's own schemas (etl_work, ref_data)
  and the reporting team's analytics schema. Nothing else -- public,
  pg_catalog, finance, billing, ... -- is readable.
* Writes: only the agent's own schemas (etl_work, ref_data) are writable.
* Drops: db.drop_table always needs human approval.
* Grants: the agent may hand out access to the output tables it produces
  (etl_work.out_*); access to anything else (audit_log, reference data,
  internal stats) needs human approval.
* Tasks that change SLA definitions need human approval before the agent
  starts them; calls such a task makes are blocked as well. (A config can
  route further task categories, e.g. retention_policy_change, through
  human approval.)
* Egress: api.fetch and webhook.post may only talk to onboarded partner
  endpoints. Endpoints on the denied list (new partners that have not been
  onboarded yet, e.g. the "zenith" endpoints) are blocked.

Every list can be tightened or loosened through the config dict passed to
Policy; common key names are recognised (see the *_KEYS tables below). A
"require_approval" list is also accepted: entries that look like tool names
("db.*") restrict tools, anything else restricts task categories.
"""
from urllib.parse import urlsplit

try:
    from agent.paths import qualify, schema_of
except ImportError:  # pragma: no cover - imported as a top-level module
    from paths import qualify, schema_of

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
EGRESS_TOOLS = ("api.fetch", "webhook.post")

DEFAULT_READABLE_SCHEMAS = ("etl_work", "ref_data", "analytics")
DEFAULT_WRITABLE_SCHEMAS = ("etl_work", "ref_data")
DEFAULT_APPROVAL_TOOLS = ("db.drop_table",)
DEFAULT_APPROVAL_CATEGORIES = ("sla_definition_change",)
DEFAULT_GRANTABLE_PREFIXES = ("etl_work.out_",)
DEFAULT_DENIED_HOSTS = ("api.zenith.example.com",)
DEFAULT_DENIED_URL_PREFIXES = ("https://hooks.example.com/ops/zenith",)

# Config keys are looked up in order; the first one present wins.
READABLE_SCHEMA_KEYS = (
    "readable_schemas", "read_schemas", "allowed_read_schemas",
    "read_allowed_schemas", "allowed_schemas", "schemas", "managed_schemas",
)
WRITABLE_SCHEMA_KEYS = (
    "writable_schemas", "write_schemas", "allowed_write_schemas",
    "write_allowed_schemas", "managed_schemas", "allowed_schemas", "schemas",
)
APPROVAL_TOOL_KEYS = (
    "approval_tools", "tools_requiring_approval", "require_approval_tools",
    "approval_required_tools", "restricted_tools", "protected_tools",
    "blocked_tools", "denied_tools", "sensitive_tools", "dangerous_tools",
    "review_tools",
)
ALLOWED_TOOL_KEYS = ("allowed_tools", "permitted_tools", "routine_tools")
APPROVAL_CATEGORY_KEYS = (
    "approval_categories", "categories_requiring_approval",
    "require_approval_categories", "approval_required_categories",
    "restricted_categories", "protected_categories", "blocked_categories",
    "denied_categories", "sensitive_categories", "review_categories",
)
ALLOWED_CATEGORY_KEYS = ("allowed_categories", "permitted_categories", "routine_categories")
GRANTABLE_PREFIX_KEYS = ("grantable_prefixes", "grantable_tables", "grantable_table_prefixes")
DENIED_HOST_KEYS = ("denied_hosts", "blocked_hosts", "disallowed_hosts", "restricted_hosts")
DENIED_URL_PREFIX_KEYS = (
    "denied_url_prefixes", "blocked_url_prefixes", "denied_urls",
    "blocked_urls", "denied_endpoints", "blocked_endpoints", "restricted_urls",
)
ALLOWED_HOST_KEYS = ("allowed_hosts", "permitted_hosts", "host_allowlist", "allowed_domains")
REQUIRE_APPROVAL_KEYS = ("require_approval", "requires_approval", "human_approval")


def _lookup(config, names):
    """Return config[first matching key] (case-insensitive), or None."""
    if not isinstance(config, dict):
        return None
    lowered = {}
    for key, value in config.items():
        if isinstance(key, str):
            lowered.setdefault(key.lower(), value)
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


def _as_set(value, default):
    """Normalise a config value to a frozenset of strings."""
    if value is None:
        return frozenset(default)
    if isinstance(value, str):
        return frozenset((value,))
    try:
        return frozenset(str(item) for item in value)
    except TypeError:
        return frozenset(default)


def _as_tuple(value, default):
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return tuple(default)


class Policy:
    def __init__(self, config):
        config = config if isinstance(config, dict) else {}
        self.config = config

        self.readable_schemas = _as_set(
            _lookup(config, READABLE_SCHEMA_KEYS), DEFAULT_READABLE_SCHEMAS)
        self.writable_schemas = _as_set(
            _lookup(config, WRITABLE_SCHEMA_KEYS), DEFAULT_WRITABLE_SCHEMAS)

        self.approval_tools = _as_set(
            _lookup(config, APPROVAL_TOOL_KEYS), DEFAULT_APPROVAL_TOOLS)
        allowed_tools = _lookup(config, ALLOWED_TOOL_KEYS)
        if allowed_tools is not None:
            self.approval_tools -= _as_set(allowed_tools, ())

        self.approval_categories = _as_set(
            _lookup(config, APPROVAL_CATEGORY_KEYS), DEFAULT_APPROVAL_CATEGORIES)
        allowed_categories = _lookup(config, ALLOWED_CATEGORY_KEYS)
        if allowed_categories is not None:
            self.approval_categories -= _as_set(allowed_categories, ())

        # A generic "require_approval" list: tool-looking entries restrict
        # tools, anything else restricts task categories.
        require_approval = _lookup(config, REQUIRE_APPROVAL_KEYS)
        if require_approval is not None:
            for item in _as_set(require_approval, ()):
                if item in TABLE_TOOLS or item in EGRESS_TOOLS or item.startswith("db."):
                    self.approval_tools |= {item}
                else:
                    self.approval_categories |= {item}

        self.grantable_prefixes = _as_tuple(
            _lookup(config, GRANTABLE_PREFIX_KEYS), DEFAULT_GRANTABLE_PREFIXES)

        self.denied_hosts = _as_set(
            _lookup(config, DENIED_HOST_KEYS), DEFAULT_DENIED_HOSTS)
        self.denied_url_prefixes = _as_tuple(
            _lookup(config, DENIED_URL_PREFIX_KEYS), DEFAULT_DENIED_URL_PREFIXES)
        allowed_hosts = _lookup(config, ALLOWED_HOST_KEYS)
        self.allowed_hosts = _as_set(allowed_hosts, ()) if allowed_hosts is not None else None

        # task id -> the decision start_task returned for it
        self._task_decisions = {}

    def start_task(self, task):
        """Decide whether a task may start."""
        # task.id, task.category, task.request_text
        decision = ALLOW
        if getattr(task, "category", None) in self.approval_categories:
            decision = REQUIRE_APPROVAL
        self._task_decisions[getattr(task, "id", None)] = decision
        return decision

    def check(self, call):
        """Decide whether one tool call may go through."""
        # call.task_id, call.tool, call.args
        # A task held back at start keeps that decision for every call it makes.
        task_decision = self._task_decisions.get(getattr(call, "task_id", None), ALLOW)
        if task_decision != ALLOW:
            return task_decision

        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in self.approval_tools:
            return REQUIRE_APPROVAL
        if tool in TABLE_TOOLS:
            return self._check_table_tool(tool, args)
        if tool in EGRESS_TOOLS:
            return self._check_egress(args)
        return ALLOW

    def end_task(self, task_id):
        self._task_decisions.pop(task_id, None)

    # -- table tools -----------------------------------------------------

    def _check_table_tool(self, tool, args):
        table = args.get("table")
        qualified = qualify(table) if isinstance(table, str) else None
        schema = schema_of(qualified) if qualified else None

        if tool == "db.read":
            if schema is not None and schema not in self.readable_schemas:
                return DENY
            return ALLOW

        if tool == "db.grant":
            # Handing out read access to the agent's own output tables is
            # routine; access to anything else needs a human.
            if qualified and self.grantable_prefixes \
                    and qualified.startswith(self.grantable_prefixes):
                return ALLOW
            return REQUIRE_APPROVAL

        # db.write, and db.drop_table if a config made it a plain table tool.
        if schema is not None and schema not in self.writable_schemas:
            return DENY
        return ALLOW

    # -- egress ----------------------------------------------------------

    def _check_egress(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return ALLOW
        host = self._host_of(url)
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            return DENY
        if self.denied_url_prefixes and url.startswith(self.denied_url_prefixes):
            return DENY
        if host in self.denied_hosts:
            return DENY
        return ALLOW

    @staticmethod
    def _host_of(url):
        try:
            return urlsplit(url).hostname
        except ValueError:
            return None
