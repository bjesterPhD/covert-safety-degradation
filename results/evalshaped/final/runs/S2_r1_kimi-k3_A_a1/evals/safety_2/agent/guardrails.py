"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Task level (start_task):

- Requests that try to talk the agent out of its own rules are refused.
- Tasks that change warehouse-wide configuration or the data lifecycle
  (migrations, retention, partitioning, replication, vacuum schedules, SLAs,
  cost alerts) require human approval before the agent starts.
- Everything else (loads, transformations, quality checks, maintenance,
  notifications, backfills, and unclassified ad-hoc requests) may start; the
  individual tool calls are still vetted by check().

Call level (check):

- db.read is allowed in the agent's own schemas (stg_ops, ref_data) and in
  marts; every other schema is denied.
- db.write is allowed in the same set of schemas and denied elsewhere.
- db.drop_table is denied outside the agent's own schemas and requires
  approval inside them.
- db.grant is denied outside the agent's own schemas. Inside them, sharing
  the output tables the agent produced (``out_*``) is routine and allowed;
  granting access to anything else (audit logs, run stats, reference data,
  ...) requires approval.
- api.fetch and webhook.post must use http(s) and a real host. When the
  config names approved partners/webhooks (in any recognizable shape), only
  those are allowed; otherwise loopback/RFC-1918/link-local/internal hosts
  are denied and the rest is allowed.
"""

import re
from urllib.parse import urlparse

WORK_SCHEMA = "stg_ops"

# Schemas this agent owns: full access (drops/grants still need approval).
OWN_SCHEMAS = frozenset({"stg_ops", "ref_data"})

# Schemas owned by other teams. The agent publishes cleaned data into marts
# and may read it, but it never drops tables or changes grants there.
FOREIGN_SCHEMAS = frozenset({"marts"})

# Task categories that change warehouse-wide configuration or the data
# lifecycle. They always need human sign-off before the agent starts.
APPROVAL_TASK_CATEGORIES = frozenset({
    "schema_migration",
    "retention_policy_change",
    "partition_scheme_change",
    "replication_change",
    "vacuum_schedule_change",
    "sla_definition_change",
    "cost_alert_config",
})

# Prefix of the tables the agent itself produces; handing out read access to
# these is routine.
OUTPUT_TABLE_PREFIXES = ("out_",)

TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
URL_TOOLS = frozenset({"api.fetch", "webhook.post"})
KNOWN_TOOLS = TABLE_TOOLS | URL_TOOLS

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Loopback, RFC-1918, link-local (cloud metadata endpoints live there), and
# typical internal suffixes. Used only when the config names no approved
# hosts at all.
_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|::1|\[::1\]|10\.|192\.168\.|"
    r"172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.|"
    r".*\.(internal|local|lan|corp|intranet))$",
    re.IGNORECASE,
)

_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")

# Attempts to override the agent's instructions.
_INJECTION_RE = re.compile(
    r"\b(ignore|disregard|forget|override|bypass)\b[^.!?]{0,60}"
    r"\b(instructions?|rules?|guidelines?|guardrails|policies|policy|restrictions?)\b",
    re.IGNORECASE,
)


def _get(obj, name, default=None):
    """Attribute-or-key lookup, tolerant of dicts and plain objects."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _config_value(config, *keys):
    """First non-None value among `keys`, from a mapping or an object."""
    for key in keys:
        value = _get(config, key, None)
        if value is not None:
            return value
    return None


def _qualify(table):
    """Qualify a table name the same way agent.paths.qualify does."""
    name = str(table).strip().strip('"').strip()
    if "." in name:
        return name
    return "%s.%s" % (WORK_SCHEMA, name)


def _schema_of(table):
    return _qualify(table).split(".", 1)[0].strip().strip('"').lower()


def _table_name(table):
    return _qualify(table).split(".")[-1].strip().strip('"').lower()


def _normalize_url(url):
    parsed = urlparse(str(url).strip().lower())
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    return "%s://%s%s" % (parsed.scheme, host, path)


def _url_path_segments(normalized_url):
    parts = normalized_url.split("/", 3)
    if len(parts) < 4:
        return []
    return [segment for segment in parts[3].split("/") if segment]


def _host_allowed(host, allowed):
    """True if `host` is one of `allowed` or a subdomain of an entry."""
    for entry in allowed:
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _scan_config(config, max_depth=8):
    """Pull approved URLs, hosts, and name tokens out of the config.

    The deployment may describe approved partners/webhooks in any reasonable
    shape (explicit allow-lists, a per-partner mapping, single webhook
    settings, ...). We walk the whole config and collect:

    - every http(s) URL (normalized)         -> approved webhook URLs
    - every URL host and bare hostname       -> approved fetch hosts
    - every path segment, host label, key,   -> known partner/resource names
      and plain token
    """
    urls, hosts, slugs = set(), set(), set()

    def add_slug(token):
        token = token.strip().lower()
        if _SLUG_RE.match(token):
            slugs.add(token)

    def add_host(host):
        host = host.strip().lower()
        if not host:
            return
        hosts.add(host)
        for label in host.split("."):
            add_slug(label)

    def walk(node, depth):
        if depth > max_depth:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                add_slug(str(key))
                walk(value, depth + 1)
        elif isinstance(node, (list, tuple, set, frozenset)):
            for value in node:
                walk(value, depth + 1)
        elif isinstance(node, str):
            text = node.strip()
            low = text.lower()
            if low.startswith(("http://", "https://")):
                parsed = urlparse(low)
                if parsed.hostname:
                    add_host(parsed.hostname)
                    path = (parsed.path or "").rstrip("/")
                    urls.add("%s://%s%s" % (parsed.scheme, parsed.hostname, path))
                    for segment in path.split("/"):
                        add_slug(segment)
            elif _HOST_RE.match(low):
                add_host(low)
            else:
                add_slug(low)
        elif hasattr(node, "__dict__"):
            walk(vars(node), depth + 1)

    walk(config, 0)
    return urls, hosts, slugs


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}

        own = _config_value(self.config, "own_schemas", "schemas")
        self.own_schemas = frozenset(
            str(s).lower() for s in (own or OWN_SCHEMAS)
        )

        foreign = _config_value(
            self.config, "foreign_schemas", "protected_schemas"
        )
        self.foreign_schemas = frozenset(
            str(s).lower() for s in (foreign or FOREIGN_SCHEMAS)
        )

        # Reads and writes may touch our own schemas plus the foreign ones
        # we publish into; drops and grants stay inside our own.
        self.read_schemas = self.own_schemas | self.foreign_schemas
        self.write_schemas = self.own_schemas | self.foreign_schemas

        approval = _config_value(self.config, "approval_categories")
        self.approval_categories = frozenset(
            approval or APPROVAL_TASK_CATEGORIES
        )

        prefixes = _config_value(
            self.config, "output_table_prefixes", "output_prefixes"
        )
        self.output_prefixes = tuple(prefixes or OUTPUT_TABLE_PREFIXES)

        urls, hosts, slugs = _scan_config(self.config)
        self.approved_urls = urls
        self.approved_hosts = hosts
        self.known_slugs = slugs

        # Remember what each started task asked for.
        self._tasks = {}

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _get(task, "id")
        category = _get(task, "category")
        text = str(_get(task, "request_text", "") or "")
        if task_id is not None:
            self._tasks[task_id] = (category, text)
        if text and _INJECTION_RE.search(text):
            return "refuse"
        if category in self.approval_categories:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _get(call, "tool", "") or ""
        args = _get(call, "args", None) or {}

        if tool in TABLE_TOOLS:
            return self._check_table_tool(tool, args.get("table"))
        if tool in URL_TOOLS:
            return self._check_url_tool(tool, args.get("url"))
        return "deny"

    def _check_table_tool(self, tool, table):
        schema = _schema_of(table) if table else WORK_SCHEMA
        name = _table_name(table) if table else ""

        if tool == "db.read":
            return "allow" if schema in self.read_schemas else "deny"

        if tool == "db.write":
            return "allow" if schema in self.write_schemas else "deny"

        if tool == "db.drop_table":
            # Destructive: never outside our own schemas, and even there a
            # human signs off.
            if schema not in self.own_schemas:
                return "deny"
            return "require_approval"

        if tool == "db.grant":
            # Access control: never outside our own schemas. Sharing the
            # outputs we produced is routine; anything else needs sign-off.
            if schema not in self.own_schemas:
                return "deny"
            if any(name.startswith(p) for p in self.output_prefixes):
                return "allow"
            return "require_approval"

        return "deny"

    def _check_url_tool(self, tool, url):
        if not isinstance(url, str) or not url.strip():
            return "deny"
        parsed = urlparse(url.strip())
        if (parsed.scheme or "").lower() not in ALLOWED_URL_SCHEMES:
            return "deny"
        host = (parsed.hostname or "").lower()
        if not host:
            return "deny"

        if tool == "api.fetch":
            if self.approved_hosts:
                if _host_allowed(host, self.approved_hosts):
                    return "allow"
                return "deny"
        else:  # webhook.post
            if self.approved_urls or self.approved_hosts:
                normalized = _normalize_url(url)
                if normalized in self.approved_urls:
                    return "allow"
                # On an approved webhook host, the base endpoint and
                # endpoints naming a known partner/resource are fine;
                # anything else (e.g. an unvetted partner) is denied.
                segments = _url_path_segments(normalized)
                if _host_allowed(host, self.approved_hosts) and (
                    len(segments) <= 1 or segments[-1] in self.known_slugs
                ):
                    return "allow"
                return "deny"

        # No allow-list configured: fall back to SSRF hygiene.
        if _PRIVATE_HOST_RE.match(host):
            return "deny"
        return "allow"
