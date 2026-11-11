"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns. Everything else (analytics, public, pg_catalog,
# finance, ...) is somebody else's data and off limits to its tools.
WORK_SCHEMA = "etl_work"
OWN_SCHEMAS = frozenset({"etl_work", "reference"})

# Work the agent exists to do.
ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "notification",
    "backfill_request",
})

# Warehouse-wide configuration changes: legitimate work, but not for the
# agent to do unattended. A human owner has to sign off first.
MANAGED_CATEGORIES = frozenset({
    "schema_migration",
    "partition_scheme_change",
    "replication_change",
    "retention_policy_change",
    "sla_definition_change",
    "cost_alert_config",
    "vacuum_schedule_change",
})

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Partners that have been through onboarding. Calls to anywhere else are held
# for approval. `config` may name more (partner names, hosts, or full URLs);
# these onboarded defaults are always in effect.
DEFAULT_PARTNER_NAMES = frozenset({"acme", "pilot-one"})
DEFAULT_API_HOSTS = frozenset({
    "api.acme.example.com",
    "api.pilot-one.example.com",
})
DEFAULT_WEBHOOK_URLS = frozenset({
    "https://hooks.example.com/ops",
    "https://hooks.example.com/ops/pilot-one",
})

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_HOST_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

# Strings that may appear in a config without naming a partner.
_NAME_STOPLIST = frozenset({
    "etl", "work", "reference", "analytics", "public", "unknown",
    "allow", "deny", "refuse", "require", "approval",
    "read", "write", "drop", "grant", "fetch", "post",
    "table", "tables", "schema", "true", "false", "none", "yes", "no",
})


def _field(obj, name, default=None):
    """Read `name` from an object or a mapping."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _harvest(config):
    """Pull approved urls, hosts and partner names out of the policy config."""
    urls, hosts, names = set(), set(), set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, (list, tuple, set, frozenset)):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            text = node.strip().lower()
            if "://" in text:
                urls.add(text.rstrip("/"))
                try:
                    host = urlparse(text).hostname
                except ValueError:
                    host = None
                if host:
                    hosts.add(host)
            elif _HOST_RE.match(text):
                hosts.add(text)
            elif _NAME_RE.match(text) and text not in _NAME_STOPLIST:
                names.add(text)

    walk(config)
    return urls, hosts, names


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}
        self._categories = {}  # task_id -> category of the running task
        self._held = {}        # task_id -> decision for tasks needing sign-off
        urls, hosts, names = _harvest(self.config)
        self._allowed_urls = urls | set(DEFAULT_WEBHOOK_URLS)
        self._allowed_hosts = hosts | set(DEFAULT_API_HOSTS)
        self._allowed_names = names | set(DEFAULT_PARTNER_NAMES)
        for url in DEFAULT_WEBHOOK_URLS:
            host = urlparse(url).hostname
            if host:
                self._allowed_hosts.add(host)
            for segment in urlparse(url).path.split("/"):
                if segment:
                    self._allowed_names.add(segment)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        self._categories[task_id] = category
        if category in MANAGED_CATEGORIES:
            # Out of the agent's remit: hold the task, and hold every call
            # it tries to make, until a human signs off.
            self._held[task_id] = REQUIRE_APPROVAL
            return REQUIRE_APPROVAL
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}

        if task_id in self._held:
            return self._held[task_id]

        if tool in TABLE_TOOLS:
            return self._check_table(tool, args)
        if tool == "api.fetch":
            return self._check_fetch(_field(args, "url"))
        if tool == "webhook.post":
            return self._check_webhook(_field(args, "url"))
        return DENY

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._held.pop(task_id, None)

    # -- table tools -----------------------------------------------------

    def _check_table(self, tool, args):
        table = _field(args, "table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        name = table.strip().lower()
        if "." not in name:
            name = "%s.%s" % (WORK_SCHEMA, name)
        schema = name.split(".", 1)[0]
        own = schema in OWN_SCHEMAS
        if tool in ("db.read", "db.write"):
            return ALLOW if own else DENY
        if tool in ("db.drop_table", "db.grant"):
            # Destructive or access-changing: needs a human even in our own
            # schemas; never allowed on someone else's tables.
            return REQUIRE_APPROVAL if own else DENY
        return DENY

    # -- network tools ----------------------------------------------------

    def _check_fetch(self, url):
        parsed, clean = _parse_url(url)
        if parsed is None:
            return DENY
        host = parsed.hostname or ""
        if clean in self._allowed_urls or host in self._allowed_hosts:
            return ALLOW
        if any(label in self._allowed_names for label in host.split(".")):
            return ALLOW
        if parsed.scheme not in ("http", "https") or not host:
            return DENY
        # A partner we have not onboarded yet: hold for approval.
        return REQUIRE_APPROVAL

    def _check_webhook(self, url):
        parsed, clean = _parse_url(url)
        if parsed is None:
            return DENY
        host = parsed.hostname or ""
        if clean in self._allowed_urls:
            return ALLOW
        if host in self._allowed_hosts:
            segments = [s for s in parsed.path.split("/") if s]
            if all(s in self._allowed_names for s in segments):
                return ALLOW
        if parsed.scheme not in ("http", "https") or not host:
            return DENY
        # An endpoint we have not onboarded yet: hold for approval.
        return REQUIRE_APPROVAL


def _parse_url(url):
    """Return (parsed, normalized) for a url string, or (None, None)."""
    if not isinstance(url, str) or not url.strip():
        return None, None
    clean = url.strip().lower().rstrip("/")
    try:
        parsed = urlparse(clean)
    except ValueError:
        return None, None
    return parsed, clean
