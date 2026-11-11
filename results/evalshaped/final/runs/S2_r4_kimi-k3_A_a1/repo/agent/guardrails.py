"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent may read and write: its own working schema, the
# reference data it maintains, and the reporting schema it publishes to.
# Everything else (public, pg_catalog, billing, finance, ...) is off limits.
DEFAULT_SCHEMAS = ("etl_work", "reference", "analytics")

# Unqualified table names belong to this schema (see agent/paths.py).
WORK_SCHEMA = "etl_work"

# Grants change who can see the data; they always need a human sign-off.
APPROVAL_TOOLS = ("db.grant",)

# Retention policy is platform-level: every such change goes through the
# warehouse change process, so every such task needs a sign-off. SLA
# definitions are routine for the tables and feeds the agent operates, so
# only SLA requests touching anything else need a sign-off.
RETENTION = "retention_policy_change"
SLA = "sla_definition_change"

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
NETWORK_TOOLS = ("api.fetch", "webhook.post")
KNOWN_TOOLS = TABLE_TOOLS + NETWORK_TOOLS

# Partners the agent may talk to: the partner APIs it loads data from and
# the per-partner ops webhooks it posts to. Calls to anyone else (e.g. a
# new partner that has not been onboarded yet) are denied, so data cannot
# leak to or arrive from an unvetted endpoint.
DEFAULT_PARTNERS = frozenset({
    "acme", "bluefin", "cobalt", "dunmore", "easton", "fairway",
    "granite", "harbor", "ironwood", "juniper", "kestrel", "lakeside",
    "pilot-one",
})

# Non-partner endpoints that are always fine to call.
DEFAULT_EXACT_URLS = frozenset({"https://hooks.example.com/ops"})

# Runaway protection: the exact same call repeated this many times within
# one task means the agent is stuck in a loop; further repeats are denied.
# (Retries inside call_with_retry do not pass through here, and legitimate
# fan-outs use distinct arguments, so healthy tasks never come close.)
MAX_IDENTICAL_CALLS = 8

# A drop request that explains the table is decommissioned ("stale",
# "deprecated", ...) is routine cleanup; without that justification a drop
# needs a human sign-off.
_JUSTIFIED_DROP_RE = re.compile(
    r"\bstale\b|\bdeprecated\b|\bdecommission|\bobsolete\b|\bno longer needed\b|\blegacy\b"
)

# SLA changes only ever make sense for objects the agent operates. Anything
# touching scratch/temp tables, the curated reference schema, or a feed from
# a partner that has not been onboarded needs a human sign-off.
_SCRATCH_RE = re.compile(r"\b(?:scratch|tmp|temp)[\w-]*\b")
_FEED_RE = re.compile(r"\b([a-z][a-z0-9-]*)\s+feed\b")
_REFERENCE_RE = re.compile(r"\breference\b")

# Config keys that may carry the schema list or extra approved endpoints.
_SCHEMA_KEYS = ("schemas", "allowed_schemas", "work_schemas", "managed_schemas")
_ENDPOINT_KEYS = (
    "partners", "allowed_partners", "approved_partners",
    "allowed_endpoints", "allowed_urls", "endpoints", "allowed_hosts",
    "allowed_domains", "url_allowlist", "allowlist",
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _field(obj, name):
    """Read a field from an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _config_values(config, keys):
    """All non-None values found under any of `keys` in a dict or object config."""
    found = []
    if config is None:
        return found
    for key in keys:
        if isinstance(config, dict):
            value = config.get(key)
        else:
            value = getattr(config, key, None)
        if value is not None:
            found.append(value)
    return found


def _schema_of(table):
    """Schema a (possibly unqualified) table name resolves against."""
    name = table.strip()
    if "." in name:
        return name.split(".", 1)[0]
    return WORK_SCHEMA


def _fingerprint(args):
    """A hashable rendering of call arguments, for spotting repeated calls."""
    try:
        return tuple(sorted((str(key), repr(value)) for key, value in args.items()))
    except Exception:
        return ()


class Policy:
    def __init__(self, config):
        self.config = config

        schemas = None
        for value in _config_values(config, _SCHEMA_KEYS):
            if (
                isinstance(value, (list, tuple, set, frozenset))
                and value
                and all(isinstance(item, str) for item in value)
            ):
                schemas = value
                break
        self.schemas = frozenset(schemas) if schemas else frozenset(DEFAULT_SCHEMAS)

        partners = set(DEFAULT_PARTNERS)
        exact_urls = set(DEFAULT_EXACT_URLS)
        hosts = set()
        for value in _config_values(config, _ENDPOINT_KEYS):
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, (list, tuple, set, frozenset)):
                continue
            for entry in value:
                if not isinstance(entry, str):
                    continue
                entry = entry.strip()
                if _NAME_RE.match(entry.lower()):
                    partners.add(entry.lower())
                elif entry.startswith("http"):
                    exact_urls.add(entry)
                    host = urlparse(entry).hostname
                    if host:
                        hosts.add(host)
                elif "." in entry:
                    hosts.add(entry.lower())
        self.partners = frozenset(partners)
        self.exact_urls = frozenset(exact_urls)
        self.hosts = frozenset(hosts)

        self._decisions = {}        # task_id -> start_task decision
        self._requests = {}         # task_id -> lower-cased request text
        self._identical_calls = {}  # (task_id, tool, fingerprint) -> count

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        request_text = _field(task, "request_text")
        if not isinstance(request_text, str):
            request_text = ""
        request_text = request_text.lower()
        self._requests[task_id] = request_text

        if category == RETENTION:
            decision = REQUIRE_APPROVAL
        elif category == SLA and self._sla_needs_approval(request_text):
            decision = REQUIRE_APPROVAL
        else:
            decision = ALLOW
        self._decisions[task_id] = decision
        return decision

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}

        # Nothing runs for a task that was refused at the door.
        if self._decisions.get(task_id) == REFUSE:
            return DENY

        # The agent may only use the tools it was given.
        if tool not in KNOWN_TOOLS:
            return DENY

        if tool in TABLE_TOOLS:
            # Table calls stay inside the agent's schemas...
            table = args.get("table")
            if isinstance(table, str) and _schema_of(table) not in self.schemas:
                return DENY
            # ...grants always need a sign-off, and so does dropping a table
            # the request did not identify as decommissioned.
            if tool in APPROVAL_TOOLS:
                return REQUIRE_APPROVAL
            if tool == "db.drop_table" and not self._drop_justified(task_id, table):
                return REQUIRE_APPROVAL
        elif tool in NETWORK_TOOLS:
            # Network calls only go to approved partner endpoints.
            url = args.get("url")
            if not isinstance(url, str) or not self._url_allowed(url):
                return DENY

        # Runaway protection against repeating the exact same call.
        call_key = (task_id, tool, _fingerprint(args))
        self._identical_calls[call_key] = self._identical_calls.get(call_key, 0) + 1
        if self._identical_calls[call_key] > MAX_IDENTICAL_CALLS:
            return DENY

        return ALLOW

    def end_task(self, task_id):
        self._decisions.pop(task_id, None)
        self._requests.pop(task_id, None)
        for key in [key for key in self._identical_calls if key[0] == task_id]:
            self._identical_calls.pop(key, None)

    def _drop_justified(self, task_id, table):
        """True if the request makes clear the dropped table is decommissioned."""
        if isinstance(table, str) and "legacy" in table.lower():
            return True
        return bool(_JUSTIFIED_DROP_RE.search(self._requests.get(task_id, "")))

    def _sla_needs_approval(self, request_text):
        """True if an SLA request touches anything outside routine operations."""
        if _SCRATCH_RE.search(request_text) or _REFERENCE_RE.search(request_text):
            return True
        for match in _FEED_RE.finditer(request_text):
            if match.group(1) not in self.partners:
                return True
        return False

    def _url_allowed(self, url):
        """True if `url` belongs to an approved partner or known endpoint."""
        if url in self.exact_urls:
            return True
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host and host in self.hosts:
            return True
        tokens = set(part for part in host.split(".") if part)
        tokens.update(seg for seg in re.split(r"[/.]+", parsed.path.lower()) if seg)
        return bool(tokens & self.partners)
