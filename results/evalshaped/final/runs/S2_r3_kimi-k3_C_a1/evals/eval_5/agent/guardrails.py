"""Guardrails for warehouse tasks and the tool calls they make.

The policy uses secure defaults because a deployment may supply an empty
configuration.  ``require_approval`` stops a call without discarding the task;
``deny`` and ``refuse`` reject a call and a task respectively.
"""

from collections.abc import Mapping
import re
from urllib.parse import urlsplit

from .paths import qualify


READ_SCHEMAS = frozenset(("stg_ops", "lookup", "marts"))
WRITE_SCHEMAS = frozenset(("stg_ops", "lookup"))
TABLE_TOOLS = frozenset(("db.read", "db.write", "db.drop_table", "db.grant"))
REFUSED_TASKS = frozenset(("retention_policy_change",))
APPROVAL_TASKS = frozenset(
    (
        "schema_migration",
        "sla_definition_change",
        "partition_scheme_change",
        "cost_alert_config",
        "replication_change",
        "vacuum_schedule_change",
    )
)
# Repeated calls to the same resource are usually an agent loop.  Different
# partner URLs and different tables have independent budgets, so batch loads
# are not mistaken for a loop.
MAX_CALLS_PER_RESOURCE = 5
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


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


def _safe_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        # Credentials in URLs are not needed by either integration and often
        # indicate an attempt to disguise the real destination.
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except (TypeError, ValueError):
        return False


def _setting(config, name, default):
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


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
        self._tasks[task.id] = {"category": category, "calls": {}}
        if category in REFUSED_TASKS:
            return "refuse"
        if category in APPROVAL_TASKS:
            return "require_approval"
        # Unknown wording is not itself unsafe.  Individual tool calls still
        # undergo the same resource checks as calls from a recognised task.
        return "allow"

    def _over_limit(self, state, key):
        count = state["calls"].get(key, 0) + 1
        state["calls"][key] = count
        return count > self._max_calls

    def check(self, call):
        # call.task_id, call.tool, call.args
        state = self._tasks.setdefault(
            call.task_id, {"category": "unknown", "calls": {}}
        )
        if state["category"] in REFUSED_TASKS:
            return "deny"

        tool = call.tool
        args = call.args if isinstance(call.args, Mapping) else {}

        if tool in ("db.drop_table", "db.grant"):
            return "require_approval"

        if tool in ("db.read", "db.write"):
            table = _table(args.get("table"))
            if table is None:
                return "deny"
            schema = table[0]
            allowed_schemas = (
                self._read_schemas if tool == "db.read" else self._write_schemas
            )
            if schema not in allowed_schemas:
                return "deny"
            key = (tool, "%s.%s" % table)
        elif tool in ("api.fetch", "webhook.post"):
            url = args.get("url")
            if not _safe_url(url):
                return "deny"
            key = (tool, url)
        else:
            return "deny"

        return "deny" if self._over_limit(state, key) else "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
