"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The default policy is deliberately small and operational:

* only categories the router knows about may start;
* table calls stay in the warehouse schemas this agent uses;
* dropping a non-temporary table or changing grants needs approval;
* partner API and webhook calls must use HTTPS and must not target local
  or otherwise obviously internal addresses.

``analytics`` is a publication target for load/transformation tasks, but not a
schema the agent may maintain destructively.
"""

import ipaddress
from urllib.parse import urlparse

from agent.paths import WORK_SCHEMA, qualify, schema_of
from agent.router import RULES


KNOWN_CATEGORIES = frozenset(category for category, _ in RULES)
OWNED_SCHEMAS = frozenset((WORK_SCHEMA, "reference"))
PUBLICATION_SCHEMAS = frozenset(("analytics",))
PUBLICATION_CATEGORIES = frozenset(("data_load", "transformation"))

READ_TOOLS = frozenset(("db.read",))
WRITE_TOOLS = frozenset(("db.write",))
DROP_TOOLS = frozenset(("db.drop_table",))
GRANT_TOOLS = frozenset(("db.grant",))
NETWORK_TOOLS = frozenset(("api.fetch", "webhook.post"))

TEMP_PREFIXES = ("tmp_", "temp_")
TEMP_SUFFIXES = ("_tmp", "_temp")


class Policy:
    def __init__(self, config):
        self.config = config
        self.owned_schemas = frozenset(
            self._config_value("owned_schemas", OWNED_SCHEMAS)
        )
        self.publication_schemas = frozenset(
            self._config_value("publication_schemas", PUBLICATION_SCHEMAS)
        )
        self.publication_categories = frozenset(
            self._config_value("publication_categories", PUBLICATION_CATEGORIES)
        )
        # Operators can make high-impact categories approval-gated through the
        # agent config without changing code.
        self.approval_categories = frozenset(
            self._config_value("approval_categories", ())
        )
        self.allowed_url_hosts = self._config_value("allowed_url_hosts", None)
        if self.allowed_url_hosts is not None:
            self.allowed_url_hosts = frozenset(
                host.lower() for host in self.allowed_url_hosts
            )
        self._task_categories = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = self._field(task, "category")
        task_id = self._field(task, "id")
        if task_id is not None:
            self._task_categories[task_id] = category

        if category not in KNOWN_CATEGORIES:
            return "refuse"
        if category in self.approval_categories:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = self._field(call, "tool", "")
        args = self._field(call, "args", {}) or {}

        if tool in READ_TOOLS:
            return self._check_read(args)
        if tool in WRITE_TOOLS:
            return self._check_write(call, args)
        if tool in DROP_TOOLS:
            return self._check_drop(args)
        if tool in GRANT_TOOLS:
            return self._check_grant(args)
        if tool in NETWORK_TOOLS:
            return self._check_url(args)

        return "deny"

    def end_task(self, task_id):
        self._task_categories.pop(task_id, None)

    def _check_read(self, args):
        schema = self._table_schema(args)
        if schema in self.owned_schemas or schema in self.publication_schemas:
            return "allow"
        return "deny"

    def _check_write(self, call, args):
        schema = self._table_schema(args)
        if schema in self.owned_schemas:
            return "allow"
        if schema in self.publication_schemas:
            task_id = self._field(call, "task_id")
            category = self._task_categories.get(task_id)
            if category in self.publication_categories:
                return "allow"
        return "deny"

    def _check_drop(self, args):
        table = args.get("table")
        schema = self._table_schema(args)
        if schema not in self.owned_schemas:
            return "deny"
        if self._is_temporary(table):
            return "allow"
        return "require_approval"

    def _check_grant(self, args):
        schema = self._table_schema(args)
        if schema in self.owned_schemas:
            return "require_approval"
        return "deny"

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"

        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return "deny"
        if parsed.username or parsed.password:
            return "deny"

        hostname = parsed.hostname.lower()
        if self.allowed_url_hosts is not None:
            return "allow" if hostname in self.allowed_url_hosts else "deny"
        if self._is_local_hostname(hostname):
            return "deny"
        return "allow"

    def _table_schema(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return None
        try:
            return schema_of(qualify(table)).lower()
        except (AttributeError, IndexError, TypeError):
            return None

    @staticmethod
    def _is_temporary(table):
        if not isinstance(table, str):
            return False
        name = table.strip().split(".")[-1].lower()
        return name.startswith(TEMP_PREFIXES) or name.endswith(TEMP_SUFFIXES)

    @staticmethod
    def _is_local_hostname(hostname):
        if hostname == "localhost" or hostname.endswith((".local", ".internal")):
            return True
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return False
        return (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )

    def _config_value(self, name, default):
        config = self.config
        if isinstance(config, dict):
            return config.get(name, default)
        return getattr(config, name, default)

    @staticmethod
    def _field(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
