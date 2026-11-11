"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# The schemas this agent operates on. Everything else (analytics, public,
# pg_catalog, ...) belongs to other teams or to the platform, and no table
# call of this agent may touch it.
DEFAULT_SCHEMAS = ("etl_work", "reference")

# Unqualified table names belong to this schema (see agent/paths.py).
WORK_SCHEMA = "etl_work"

# Requests that change platform-level policy rather than run routine
# operations. They must go through the warehouse change process, so the
# agent refuses them outright.
REFUSED_CATEGORIES = (
    "retention_policy_change",
    "sla_definition_change",
)

# Calls that never run unattended: dropping a table is irreversible and a
# grant changes who can see the data.
DENIED_TOOLS = ("db.drop_table", "db.grant")

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
NETWORK_TOOLS = ("api.fetch", "webhook.post")
KNOWN_TOOLS = TABLE_TOOLS + NETWORK_TOOLS

# Loop protection. A healthy task calls each tool a handful of times; one
# that keeps hammering the same tool (or repeats the exact same call) is
# stuck or runaway, so further calls are cut off. Retries inside
# call_with_retry do not pass through here, so normal tasks stay well
# below these numbers.
MAX_CALLS_PER_TOOL = 6
MAX_IDENTICAL_CALLS = 6

# Config keys that may carry the list of schemas the agent owns.
_SCHEMA_KEYS = ("schemas", "allowed_schemas", "work_schemas", "managed_schemas")


def _field(obj, name):
    """Read a field from an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _config_lookup(config, keys):
    """First non-None value for any of `keys` in a dict or object config."""
    if config is None:
        return None
    for key in keys:
        if isinstance(config, dict):
            if config.get(key) is not None:
                return config[key]
        else:
            value = getattr(config, key, None)
            if value is not None:
                return value
    return None


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
        schemas = _config_lookup(config, _SCHEMA_KEYS)
        if (
            isinstance(schemas, (list, tuple, set, frozenset))
            and schemas
            and all(isinstance(item, str) for item in schemas)
        ):
            self.schemas = frozenset(schemas)
        else:
            self.schemas = frozenset(DEFAULT_SCHEMAS)
        self._decisions = {}        # task_id -> start_task decision
        self._tool_calls = {}       # (task_id, tool) -> number of calls seen
        self._identical_calls = {}  # (task_id, tool, fingerprint) -> count

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        decision = REFUSE if category in REFUSED_CATEGORIES else ALLOW
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

        # Dropping tables and changing grants never run unattended.
        if tool in DENIED_TOOLS:
            return DENY

        # Table calls stay inside the agent's own schemas.
        if tool in TABLE_TOOLS:
            table = args.get("table")
            if isinstance(table, str) and _schema_of(table) not in self.schemas:
                return DENY

        # Loop protection: too many calls of one tool, or too many repeats
        # of the exact same call, means the agent is stuck.
        tool_key = (task_id, tool)
        self._tool_calls[tool_key] = self._tool_calls.get(tool_key, 0) + 1
        if self._tool_calls[tool_key] > MAX_CALLS_PER_TOOL:
            return DENY

        call_key = (task_id, tool, _fingerprint(args))
        self._identical_calls[call_key] = self._identical_calls.get(call_key, 0) + 1
        if self._identical_calls[call_key] > MAX_IDENTICAL_CALLS:
            return DENY

        return ALLOW

    def end_task(self, task_id):
        self._decisions.pop(task_id, None)
        for store in (self._tool_calls, self._identical_calls):
            for key in [key for key in store if key[0] == task_id]:
                store.pop(key, None)
