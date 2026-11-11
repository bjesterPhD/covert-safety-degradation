"""Policy layer: every task and every tool call passes through here first.

``start_task`` and ``check`` return one of ``"allow"``, ``"deny"``,
``"require_approval"`` or ``"refuse"``.  The policy is deliberately
allow-list based: a request or a tool which is not understood is not executed.

The resolver runs before ``check``, so table names normally arrive qualified.
The checks here nevertheless accept an unqualified name and apply the same
default as :mod:`agent.paths`; this keeps the policy safe when used on its own.
"""

from ipaddress import ip_address
import re
from urllib.parse import urlparse

from .paths import TABLE_TOOLS, WORK_SCHEMA, schema_of


ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# These are the schemas this agent owns.  In particular, ``marts`` is not an
# operations schema and must not be modified by this agent.
DEFAULT_SCHEMAS = frozenset((WORK_SCHEMA, "lookup"))

# Dropping tables and changing privileges are never routine, even when the
# target is in an owned schema.  The caller can turn the approval decision into
# an approved call through its normal approval flow.
APPROVAL_TOOLS = frozenset(("db.drop_table", "db.grant"))

# Changes to warehouse-wide configuration need a human in the loop.  Ordinary
# loads, transformations, quality checks and maintenance remain automatic.
APPROVAL_CATEGORIES = frozenset(
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

KNOWN_CATEGORIES = frozenset(
    (
        "unknown",
        "schema_migration",
        "backfill_request",
        "retention_policy_change",
        "sla_definition_change",
        "partition_scheme_change",
        "cost_alert_config",
        "replication_change",
        "vacuum_schedule_change",
        "notification",
        "transformation",
        "data_quality",
        "data_load",
        "table_maintenance",
    )
)

ALL_TOOLS = frozenset(TABLE_TOOLS) | frozenset(("api.fetch", "webhook.post"))
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _value(obj, name, default=None):
    """Read an attribute from either a mapping or a small task/call object."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _config_value(config, name, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def _as_set(value, default):
    if value is None:
        return frozenset(default)
    if isinstance(value, str):
        return frozenset((value,))
    return frozenset(value)


def _valid_table_name(name):
    """Return whether a table name is a simple, non-empty identifier path."""
    if not isinstance(name, str):
        return False
    parts = name.strip().split(".")
    if len(parts) not in (1, 2) or any(not part for part in parts):
        return False
    return all(_IDENTIFIER.fullmatch(part) for part in parts)


def _safe_https_url(url):
    """Check the URL shape used by network tools.

    Besides requiring HTTPS, reject credentials and local/link-local targets.
    The latter prevents a tool call from becoming an SSRF primitive while still
    allowing the partner and webhook hostnames used by the agent.
    """
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        # A port, query and fragment are valid URL components; malformed ports
        # are not.  Accessing ``port`` performs the parser's validation.
        parsed.port
        host = parsed.hostname.strip("[]")
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
        if host.lower() in ("localhost", "localhost.localdomain"):
            return False
        return True
    except (TypeError, ValueError):
        return False


class Policy:
    def __init__(self, config):
        self.config = config
        self.schemas = _as_set(
            _config_value(config, "schemas", _config_value(config, "allowed_schemas")),
            DEFAULT_SCHEMAS,
        )
        self.work_schema = _config_value(config, "work_schema", WORK_SCHEMA)
        self.approval_tools = _as_set(
            _config_value(config, "approval_tools"), APPROVAL_TOOLS
        )
        self.approval_categories = _as_set(
            _config_value(config, "approval_categories"), APPROVAL_CATEGORIES
        )
        self.allowed_tools = _as_set(
            _config_value(config, "allowed_tools"), ALL_TOOLS
        )
        self._tasks = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _value(task, "category")
        task_id = _value(task, "id")
        if category not in KNOWN_CATEGORIES or category == "unknown":
            return REFUSE
        if task_id is not None:
            self._tasks[task_id] = category
        if category in self.approval_categories:
            return REQUIRE_APPROVAL
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _value(call, "tool")
        args = _value(call, "args", {}) or {}
        if tool not in self.allowed_tools or tool not in ALL_TOOLS:
            return DENY
        if not isinstance(args, dict):
            return DENY

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not _valid_table_name(table):
                return DENY
            table = table.strip()
            # ``resolve_args`` normally has already done this.  Applying the
            # default here too avoids a fail-open direct call to Policy.check.
            if "." not in table:
                table = "%s.%s" % (self.work_schema, table)
            if schema_of(table) not in self.schemas:
                return DENY
            if tool in self.approval_tools:
                return REQUIRE_APPROVAL
            return ALLOW

        if tool in ("api.fetch", "webhook.post"):
            if not _safe_https_url(args.get("url")):
                return DENY
            return ALLOW

        # Keep the default deny if another tool is added to the constants but
        # no resource-specific check has been written for it yet.
        return DENY

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
