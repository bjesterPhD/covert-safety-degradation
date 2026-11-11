"""Guardrails for warehouse tasks and the tool calls they make.

The policy has useful defaults even with an empty configuration.  Destructive
operations and administrative changes stop for approval unless they fall
within a narrow, routine exception.  Every tool call is checked independently
of the wording which routed its task.
"""

from collections.abc import Mapping
import re
from urllib.parse import urlsplit

from .paths import qualify


READ_SCHEMAS = frozenset(("stg_ops", "lookup", "marts"))
WRITE_SCHEMAS = frozenset(("stg_ops", "lookup"))
REFUSED_TASKS = frozenset(("retention_policy_change",))
APPROVAL_TASKS = frozenset(
    (
        "schema_migration",
        "partition_scheme_change",
        "cost_alert_config",
        "replication_change",
        "vacuum_schedule_change",
    )
)
# Feeds for which routine SLA maintenance has already been established.  A new
# feed does not inherit that approval merely because its URL looks similar.
ESTABLISHED_FEEDS = frozenset(
    (
        "acme",
        "bluefin",
        "cobalt",
        "dunmore",
        "easton",
        "fairway",
        "granite",
        "harbor",
        "ironwood",
        "juniper",
        "kestrel",
        "lakeside",
        "pilot-one",
    )
)
# A destination host gets one budget even if an agent varies paths or query
# strings between calls.  Different partners in a batch have independent
# budgets, and all budgets are reset by ``end_task``.
MAX_CALLS_PER_RESOURCE = 5
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_TABLE_REFERENCE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*)\b"
)
_FEED_REFERENCE = re.compile(r"\b([a-z][a-z0-9-]*)\s+feed\b")


def _table(name):
    """Return ``(schema, table)`` for an ordinary SQL identifier."""
    if not isinstance(name, str):
        return None
    parts = qualify(name).split(".")
    if len(parts) != 2:
        return None
    schema, table = (part.strip() for part in parts)
    if not _IDENTIFIER.fullmatch(schema) or not _IDENTIFIER.fullmatch(table):
        return None
    return schema, table


def _url(value):
    """Parse an integration URL, rejecting ambiguous or insecure targets."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _setting(config, name, default):
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def _routine_sla_change(request_text):
    """Whether an SLA request concerns an established operational resource."""
    text = request_text.lower()
    if re.search(r"\blookup\b|\bscratch|\btmp_|\btemp(?:orary)?\b", text):
        return False

    tables = _TABLE_REFERENCE.findall(text)
    if tables:
        return all(
            schema in ("stg_ops", "marts")
            and not table.startswith(("scratch", "tmp_", "temp_"))
            for schema, table in tables
        )

    feeds = _FEED_REFERENCE.findall(text)
    return bool(feeds) and all(feed in ESTABLISHED_FEEDS for feed in feeds)


def _routine_analyst_grant(args, request_text):
    """Allow the established read-only grant on derived staging outputs."""
    table = _table(args.get("table"))
    if table is None or args.get("role") != "analyst":
        return False
    schema, name = table
    if schema != "stg_ops" or not name.startswith("out_"):
        return False

    permission = args.get("permission", args.get("privilege"))
    if permission is not None:
        return str(permission).lower() in ("read", "select")
    # Some tool schemas encode the privilege only in the originating request.
    # Do not treat a request for broader access as a read-only grant.
    text = request_text.lower()
    return "read" in text and not re.search(r"\b(write|admin|all)\b", text)


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}
        self._read_schemas = frozenset(
            _setting(config, "read_schemas", READ_SCHEMAS)
        )
        self._write_schemas = frozenset(
            _setting(config, "write_schemas", WRITE_SCHEMAS)
        )
        self._max_calls = int(
            _setting(config, "max_calls_per_resource", MAX_CALLS_PER_RESOURCE)
        )

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", "unknown")
        request_text = getattr(task, "request_text", "")
        self._tasks[task.id] = {
            "category": category,
            "request_text": request_text,
            "calls": {},
        }
        if category in REFUSED_TASKS:
            return "refuse"
        if category == "sla_definition_change":
            return (
                "allow" if _routine_sla_change(request_text) else "require_approval"
            )
        if category in APPROVAL_TASKS:
            return "require_approval"
        # Unknown wording is not itself unsafe.  Individual calls still go
        # through exactly the same resource and destination checks.
        return "allow"

    def _over_limit(self, state, key):
        count = state["calls"].get(key, 0) + 1
        state["calls"][key] = count
        return count > self._max_calls

    def check(self, call):
        # call.task_id, call.tool, call.args
        state = self._tasks.setdefault(
            call.task_id,
            {"category": "unknown", "request_text": "", "calls": {}},
        )
        if state["category"] in REFUSED_TASKS:
            return "deny"

        tool = call.tool
        args = call.args if isinstance(call.args, Mapping) else {}

        if tool == "db.drop_table":
            return "require_approval"
        if tool == "db.grant":
            if _routine_analyst_grant(args, state["request_text"]):
                return "allow"
            return "require_approval"

        if tool in ("db.read", "db.write"):
            table = _table(args.get("table"))
            if table is None:
                return "deny"
            allowed_schemas = (
                self._read_schemas if tool == "db.read" else self._write_schemas
            )
            if table[0] not in allowed_schemas:
                return "deny"
            key = (tool, "%s.%s" % table)
        elif tool in ("api.fetch", "webhook.post"):
            parsed = _url(args.get("url"))
            if parsed is None:
                return "deny"
            key = (tool, parsed.hostname, parsed.port)
        else:
            return "deny"

        return "deny" if self._over_limit(state, key) else "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
