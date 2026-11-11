"""Policy layer for tasks and tool calls.

``start_task`` and ``check`` return one of ``allow``, ``deny``,
``require_approval`` and ``refuse``.  The policy is deliberately independent
of the tool implementations: a tool call which is not explicitly allowed is
not executed.
"""

from collections.abc import Mapping
import re
from urllib.parse import urlsplit

from .paths import qualify


DEFAULT_SCHEMAS = frozenset(("stg_ops", "lookup"))
ROUTINE_TASKS = frozenset(
    (
        "data_load",
        "backfill_request",
        "transformation",
        "data_quality",
        "table_maintenance",
        "notification",
    )
)
ADMIN_TASKS = frozenset(
    (
        "schema_migration",
        "retention_policy_change",
        "sla_definition_change",
        "partition_scheme_change",
        "cost_alert_config",
        "replication_change",
        "vacuum_schedule_change",
    )
)
TABLE_TOOLS = frozenset(("db.read", "db.write", "db.drop_table", "db.grant"))
DESTRUCTIVE_TOOLS = frozenset(("db.drop_table", "db.grant"))
DEFAULT_MAX_CALLS = 6
_MISSING = object()
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _setting(config, names, default=_MISSING):
    """Read the first present setting from a mapping or a config object."""
    for name in names:
        if isinstance(config, Mapping) and name in config:
            return config[name]
        if hasattr(config, name):
            return getattr(config, name)
    return default if default is not _MISSING else _MISSING


def _entries(value):
    """Normalize a configured allowlist to a tuple of strings."""
    if value is _MISSING or value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        # A mapping is commonly used as a set-like allowlist.  Its keys are
        # the resources; values may hold unrelated metadata.
        return tuple(str(item) for item in value)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return (str(value),)


def _valid_table(name, schemas):
    if not isinstance(name, str):
        return False
    qualified = qualify(name)
    parts = qualified.split(".")
    if len(parts) != 2:
        return False
    schema, table = (part.strip() for part in parts)
    return (
        schema in schemas
        and bool(_IDENTIFIER.fullmatch(schema))
        and bool(_IDENTIFIER.fullmatch(table))
    )


def _host_matches(hostname, pattern):
    """Match a host exactly, or a subdomain for an explicit ``*.`` rule."""
    pattern = pattern.strip().lower().rstrip(".")
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return hostname.endswith(suffix) and hostname != pattern[2:]
    return hostname == pattern


def _same_origin(parsed, allowed):
    default_ports = {"https": 443, "http": 80}
    parsed_port = parsed.port or default_ports.get(parsed.scheme)
    allowed_port = allowed.port or default_ports.get(allowed.scheme)
    return (
        parsed.scheme == allowed.scheme
        and parsed.hostname == allowed.hostname
        and parsed_port == allowed_port
    )


def _path_prefix(path, prefix):
    if not prefix or prefix == "/":
        return True
    path = path or "/"
    prefix = prefix or "/"
    if path == prefix:
        return True
    return path.startswith(prefix if prefix.endswith("/") else prefix + "/")


def _url_matches(url, entry, exact=False):
    """Match an allowlist entry without treating a host as a URL suffix."""
    entry = entry.strip()
    if not entry:
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False

        if "://" in entry:
            allowed = urlsplit(entry)
            if allowed.scheme != "https" or not allowed.hostname:
                return False
            if not _same_origin(parsed, allowed):
                return False
            if exact:
                return (parsed.path or "/").rstrip("/") == (
                    allowed.path or "/"
                ).rstrip("/")
            return _path_prefix(parsed.path, allowed.path)

        # A host-only entry may optionally be written as host/path.
        host, separator, path = entry.partition("/")
        if not _host_matches(parsed.hostname, host):
            return False
        if separator:
            prefix = "/" + path
            if exact:
                return (parsed.path or "/").rstrip("/") == prefix.rstrip("/")
            return _path_prefix(parsed.path, prefix)
        return True
    except (TypeError, ValueError):
        return False


def _url_allowed(url, urls=(), prefixes=(), hosts=(), exact_urls=False):
    if not isinstance(url, str):
        return False
    if urls and any(_url_matches(url, item, exact=exact_urls) for item in urls):
        return True
    if prefixes and any(_url_matches(url, item, exact=False) for item in prefixes):
        return True
    # Host rules are a fallback.  A more specific configured URL rule must not
    # be broadened accidentally by a generic host rule.
    if not urls and not prefixes and hosts:
        return any(_url_matches(url, item) for item in hosts)
    return False


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

        configured_schemas = _setting(
            config,
            ("allowed_schemas", "schema_allowlist", "schemas", "work_schemas"),
            DEFAULT_SCHEMAS,
        )
        self._schemas = frozenset(_entries(configured_schemas))

        self._api_urls = _entries(
            _setting(
                config,
                (
                    "allowed_api_urls",
                    "api_url_allowlist",
                    "api_urls",
                    "api_allowlist",
                    "allowed_apis",
                ),
                (),
            )
        )
        self._api_hosts = _entries(
            _setting(
                config,
                (
                    "allowed_api_hosts",
                    "api_host_allowlist",
                    "api_hosts",
                    "allowed_api_domains",
                    "api_domains",
                ),
                (),
            )
        )
        self._webhook_urls = _entries(
            _setting(
                config,
                (
                    "allowed_webhook_urls",
                    "webhook_url_allowlist",
                    "webhook_urls",
                    "webhook_allowlist",
                    "allowed_webhooks",
                ),
                (),
            )
        )
        self._webhook_prefixes = _entries(
            _setting(
                config,
                ("allowed_webhook_prefixes", "webhook_prefixes"),
                (),
            )
        )
        self._webhook_hosts = _entries(
            _setting(
                config,
                ("allowed_webhook_hosts", "webhook_host_allowlist", "webhook_hosts"),
                (),
            )
        )

        limit = _setting(
            config,
            (
                "max_calls_per_task",
                "max_tool_calls_per_task",
                "max_tool_calls",
                "tool_call_limit",
                "max_calls",
            ),
            DEFAULT_MAX_CALLS,
        )
        if limit is None:
            limit = DEFAULT_MAX_CALLS
        try:
            self._max_calls = max(0, int(limit))
        except (TypeError, ValueError):
            self._max_calls = DEFAULT_MAX_CALLS

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = task.id
        category = getattr(task, "category", "unknown")
        self._tasks[task_id] = {"calls": 0, "category": category}

        if category in ADMIN_TASKS or category not in ROUTINE_TASKS:
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        state = self._tasks.setdefault(
            call.task_id, {"calls": 0, "category": "unknown"}
        )
        if state["category"] in ADMIN_TASKS:
            return "deny"

        state["calls"] += 1
        if state["calls"] > self._max_calls:
            return "deny"

        tool = call.tool
        args = call.args if isinstance(call.args, Mapping) else {}

        if tool in DESTRUCTIVE_TOOLS:
            return "deny"

        if tool in TABLE_TOOLS:
            return "allow" if _valid_table(args.get("table"), self._schemas) else "deny"

        if tool == "api.fetch":
            allowed = _url_allowed(
                args.get("url"), urls=self._api_urls, hosts=self._api_hosts
            )
            return "allow" if allowed else "deny"

        if tool == "webhook.post":
            allowed = _url_allowed(
                args.get("url"),
                urls=self._webhook_urls,
                prefixes=self._webhook_prefixes,
                hosts=self._webhook_hosts,
                exact_urls=True,
            )
            return "allow" if allowed else "deny"

        return "deny"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
