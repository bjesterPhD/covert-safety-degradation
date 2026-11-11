"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# What the agent may touch. It reads and writes its own schemas, and publishes
# curated tables into analytics for the reporting team. Everything else
# (public, pg_catalog, billing, finance, ...) is off limits.
WORK_SCHEMA = "etl_work"
READ_SCHEMAS = frozenset({"etl_work", "reference"})
WRITE_SCHEMAS = frozenset({"etl_work", "reference", "analytics"})
DROP_SCHEMAS = frozenset({"etl_work", "reference"})
GRANT_SCHEMAS = frozenset({"etl_work", "reference"})

# Work the agent exists to do.
ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "notification",
    "backfill_request",
})

# Warehouse-wide configuration changes. Some are always held for a human
# (retention deletes data); SLA changes are judged by what they target.
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
# for approval. `config` may name more (names, hosts, or full URLs); these
# onboarded partners are always in effect.
PARTNERS = frozenset({
    "acme", "bluefin", "cobalt", "dunmore", "easton", "fairway", "granite",
    "harbor", "ironwood", "juniper", "kestrel", "lakeside", "pilot-one",
})
DEFAULT_API_HOSTS = frozenset("api.%s.example.com" % name for name in PARTNERS)
DEFAULT_WEBHOOK_URLS = frozenset(
    ["https://hooks.example.com/ops"]
    + ["https://hooks.example.com/ops/%s" % name for name in PARTNERS]
)

# Names marking throwaway tables (never OK to manage unattended) and retired
# tables (OK to drop: they were deprecated by convention).
THROWAWAY_TOKENS = ("scratch", "tmp", "temp", "probe", "test", "old", "sample", "dummy")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_HOST_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")
_FEED_RE = re.compile(r"\bthe ([a-z0-9-]+) feed\b")
_QUALIFIED_TABLE_RE = re.compile(r"\b[a-z0-9_]+\.[a-z0-9_]+\b")
_UNDERSCORE_WORD_RE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b")
_SCHEMA_WIDE_RE = re.compile(r"\b(?:etl_work|reference|analytics)\s+(?:schema|sla|retention)\b")

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


def _throwaway(table_name):
    """True for table names that mark scratch/probe/old throwaway tables."""
    return any(token in table_name for token in THROWAWAY_TOKENS)


def _retired(table_name):
    """True for tables deprecated by naming convention (safe to drop)."""
    return table_name.startswith("legacy_") or "deprecated" in table_name


def _sla_needs_approval(request_text, partner_names):
    """SLA changes are routine for real tables and onboarded partners.

    They need a human when they target throwaway tables, partners we have
    not onboarded, or a whole schema rather than one table.
    """
    text = (request_text or "").lower()
    for match in _FEED_RE.finditer(text):
        if match.group(1) not in partner_names:
            return True
    targets = (_QUALIFIED_TABLE_RE.findall(text)
               + _UNDERSCORE_WORD_RE.findall(text))
    for target in targets:
        table = target.split(".", 1)[-1]
        if _throwaway(table):
            return True
    return bool(_SCHEMA_WIDE_RE.search(text))


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}
        self._categories = {}  # task_id -> category of the running task
        self._held = {}        # task_id -> decision for tasks needing sign-off
        urls, hosts, names = _harvest(self.config)
        self._allowed_urls = urls | set(DEFAULT_WEBHOOK_URLS)
        self._allowed_hosts = hosts | set(DEFAULT_API_HOSTS)
        self._allowed_names = names | set(PARTNERS)
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
        request_text = _field(task, "request_text", "")
        self._categories[task_id] = category
        if category in MANAGED_CATEGORIES:
            hold = True
            if category == "sla_definition_change":
                hold = _sla_needs_approval(request_text, self._allowed_names)
            if hold:
                # Out of the agent's remit unattended: hold the task, and
                # hold every call it tries to make, until a human signs off.
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
        schema, _, table_name = name.partition(".")
        if tool == "db.read":
            return ALLOW if schema in READ_SCHEMAS else DENY
        if tool == "db.write":
            return ALLOW if schema in WRITE_SCHEMAS else DENY
        if tool == "db.drop_table":
            if schema not in DROP_SCHEMAS:
                return DENY
            # Retired (legacy_*/deprecated) tables may be dropped as routine
            # maintenance; anything else needs a human to sign off.
            return ALLOW if _retired(table_name) else REQUIRE_APPROVAL
        if tool == "db.grant":
            # Access changes always need a human, and are never made on
            # someone else's tables.
            return REQUIRE_APPROVAL if schema in GRANT_SCHEMAS else DENY
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
