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

# Task categories that change platform-level policy. The agent may prepare
# them, but a human has to sign off before anything runs.
APPROVAL_CATEGORIES = (
    "retention_policy_change",
    "sla_definition_change",
)

# Calls with irreversible or access-changing effects: dropping a table
# cannot be undone and a grant changes who can see the data. They need a
# human sign-off.
APPROVAL_TOOLS = ("db.drop_table", "db.grant")

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
        self._identical_calls = {}  # (task_id, tool, fingerprint) -> count

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        if category in APPROVAL_CATEGORIES:
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

        # Table calls stay inside the agent's schemas...
        if tool in TABLE_TOOLS:
            table = args.get("table")
            if isinstance(table, str) and _schema_of(table) not in self.schemas:
                return DENY
            # ...and destructive or access-changing calls need a sign-off.
            if tool in APPROVAL_TOOLS:
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
        for key in [key for key in self._identical_calls if key[0] == task_id]:
            self._identical_calls.pop(key, None)

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
