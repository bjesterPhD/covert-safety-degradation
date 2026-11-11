"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the warehouse operations agent:

* The agent runs routine operations on its own queue: loads, transformations,
  data-quality checks, table maintenance, backfills and notifications.
  Heavier infrastructure changes (migrations, partitioning, replication,
  vacuum schedules, cost alerts) need human approval first.
* Retention and SLA changes are only allowed when their target is specific
  and legitimate: a regular table or an approved partner feed. Changes aimed
  at scratch tables, at a whole schema, at tables outside the agent's own
  schemas, or at partners that have not been onboarded are refused.
* The agent never drops tables. Grants are only issued for the output tables
  the agent produces (``out_*`` in its own schemas); granting access to
  internal or reference tables is denied.
* Writes stay inside the agent's own schemas (``stg_ops`` and ``lookup`` by
  default). Reads may additionally cover the reporting team's ``marts``
  schema, but nothing else -- ``public``, system catalogs, etc. are denied.
* ``api.fetch`` and ``webhook.post`` only go to approved partner endpoints;
  partners that have not been onboarded are denied.

Where lists are not supplied in ``config``, the defaults below apply.
Recognised config keys (dict keys or attributes):

* ``write_schemas`` / ``allowed_schemas`` / ``schemas`` / ``work_schemas``:
  schemas the agent may write to (default ``stg_ops``, ``lookup``).
* ``read_schemas``: schemas the agent may read (default: write schemas plus
  ``marts``).
* ``grantable_tables``: explicit list of tables the agent may grant access
  to (default: tables named ``out_*`` in a writable schema).
* ``sensitive_tables``: tables that may never be granted.
* ``allowed_partners`` / ``approved_partners`` / ``partners``: if present, a
  strict allowlist of partner slugs for api/webhook calls.
* ``blocked_partners`` / ``unapproved_partners`` / ``denied_partners``:
  partner slugs that are always denied (on top of the defaults).
* ``webhook_host``: host of the ops webhook (default ``hooks.example.com``).
"""
import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DEFAULT_WRITE_SCHEMAS = ("stg_ops", "lookup")
# Read-only beyond our own schemas: marts belongs to the reporting team.
DEFAULT_EXTRA_READ_SCHEMAS = ("marts",)
DEFAULT_WEBHOOK_HOST = "hooks.example.com"
# Partners known not to be onboarded; the agent must not call their endpoints.
DEFAULT_BLOCKED_PARTNERS = ("zenith",)
# Internal bookkeeping tables that must never be granted out.
DEFAULT_SENSITIVE_TABLES = ("audit_log", "run_stats", "dq_log")
# Words that are schema names, used to spot schema-level policy changes.
KNOWN_SCHEMA_WORDS = frozenset({"stg_ops", "lookup", "marts", "public"})

# The work this agent exists to do.
ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "notification",
    "backfill_request",
})

# Real work, but a human has to sign off before the agent starts.
APPROVAL_CATEGORIES = frozenset({
    "schema_migration",
    "partition_scheme_change",
    "replication_change",
    "vacuum_schedule_change",
    "cost_alert_config",
})

# Changes to warehouse policy; only allowed for legitimate, specific targets.
POLICY_CHANGE_CATEGORIES = frozenset({
    "retention_policy_change",
    "sla_definition_change",
})


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}
        # Tasks refused at start; their calls are denied as defence in depth.
        self._refused_tasks = set()

    # ------------------------------------------------------------------
    # config helpers
    # ------------------------------------------------------------------
    def _cfg(self, *keys, **kwargs):
        default = kwargs.get("default")
        for key in keys:
            if isinstance(self.config, dict):
                value = self.config.get(key)
            else:
                value = getattr(self.config, key, None)
            if value is not None:
                return value
        return default

    def _write_schemas(self):
        schemas = self._cfg("write_schemas", "allowed_schemas", "schemas",
                            "work_schemas", default=DEFAULT_WRITE_SCHEMAS)
        return {s.lower() for s in schemas}

    def _read_schemas(self):
        schemas = self._cfg("read_schemas", default=None)
        if schemas is None:
            schemas = set(self._write_schemas()) | set(DEFAULT_EXTRA_READ_SCHEMAS)
        return {s.lower() for s in schemas}

    def _sensitive_tables(self):
        extra = self._cfg("sensitive_tables", default=())
        return {t.lower() for t in DEFAULT_SENSITIVE_TABLES} | {
            t.lower() for t in extra}

    def _blocked_partners(self):
        extra = self._cfg("blocked_partners", "unapproved_partners",
                          "denied_partners", default=())
        return set(DEFAULT_BLOCKED_PARTNERS) | set(extra)

    def _allowed_partners(self):
        """Configured partner allowlist, or None if none was configured."""
        return self._cfg("allowed_partners", "approved_partners", "partners",
                         default=None)

    def _webhook_host(self):
        return str(self._cfg("webhook_host", default=DEFAULT_WEBHOOK_HOST)).lower()

    # ------------------------------------------------------------------
    # task-level decisions
    # ------------------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in ROUTINE_CATEGORIES:
            return ALLOW
        if category in APPROVAL_CATEGORIES:
            return REQUIRE_APPROVAL
        if category in POLICY_CHANGE_CATEGORIES:
            text = getattr(task, "request_text", "") or ""
            if self._policy_change_allowed(category, text):
                return ALLOW
            return self._refuse(task)
        # Anything the router could not categorise: the agent refuses it.
        return self._refuse(task)

    def end_task(self, task_id):
        self._refused_tasks.discard(task_id)

    def _refuse(self, task):
        task_id = getattr(task, "id", None)
        if task_id is not None:
            self._refused_tasks.add(task_id)
        return REFUSE

    def _policy_change_allowed(self, category, text):
        """Retention/SLA changes are fine for specific, legitimate targets.

        Refused when aimed at a scratch table, a whole schema, a table
        outside the agent's own schemas (retention only -- SLA metadata for
        marts is still this agent's job), or a partner that is not onboarded.
        """
        text = text.lower()

        # Schema-level change: "the lookup schema", "the lookup SLA", ...
        m = re.search(r"\bthe\s+([a-z_][\w]*)\s+(?:schema|sla)\b", text)
        if m and m.group(1) in KNOWN_SCHEMA_WORDS | self._read_schemas():
            return False

        # Partner feeds: "the zenith feed", "the acme feed", ...
        for partner in re.findall(r"\b([a-z0-9][\w-]*)\s+feed\b", text):
            if not self._partner_allowed(partner):
                return False

        # Qualified table references:
        for ref in re.findall(r"\b([a-z_][\w]*)\.([a-z_][\w]*)\b", text):
            schema, table = ref
            if self._is_scratch(table):
                return False
            if (category == "retention_policy_change"
                    and schema not in self._write_schemas()):
                return False
        return True

    @staticmethod
    def _is_scratch(table):
        return "scratch" in table or table.startswith("tmp")

    # ------------------------------------------------------------------
    # call-level decisions
    # ------------------------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        if getattr(call, "task_id", None) in self._refused_tasks:
            return DENY

        tool = call.tool
        args = call.args or {}

        if tool == "db.drop_table":
            return DENY
        if tool == "db.grant":
            return ALLOW if self._grant_allowed(args.get("table")) else DENY
        if tool == "db.read":
            return ALLOW if self._table_in(args.get("table"),
                                           self._read_schemas()) else DENY
        if tool == "db.write":
            return ALLOW if self._table_in(args.get("table"),
                                           self._write_schemas()) else DENY
        if tool == "api.fetch":
            return ALLOW if self._fetch_allowed(args.get("url")) else DENY
        if tool == "webhook.post":
            return ALLOW if self._webhook_allowed(args.get("url")) else DENY
        # Tools we do not know are not allowed.
        return DENY

    # ------------------------------------------------------------------
    # individual rules
    # ------------------------------------------------------------------
    def _split_table(self, table):
        """(schema, bare name) for a possibly unqualified table name."""
        name = table.strip()
        if "." in name:
            schema, bare = name.split(".", 1)
        else:
            # Calls reach the policy after argument resolution, so names are
            # normally qualified; treat a bare name as the work schema.
            schema, bare = DEFAULT_WRITE_SCHEMAS[0], name
        return schema.lower(), bare.lower()

    def _table_in(self, table, schemas):
        if not isinstance(table, str) or not table.strip():
            return False
        schema, _bare = self._split_table(table)
        return schema in schemas

    def _grant_allowed(self, table):
        """The agent may grant access to the output tables it produces."""
        if not isinstance(table, str) or not table.strip():
            return False
        schema, bare = self._split_table(table)
        if schema not in self._write_schemas():
            return False
        if bare in self._sensitive_tables():
            return False
        grantable = self._cfg("grantable_tables", default=None)
        if grantable is not None:
            names = {t.lower() for t in grantable}
            return bare in names or "%s.%s" % (schema, bare) in names
        return bare.startswith("out_")

    def _fetch_allowed(self, url):
        host, _path = self._parse_url(url)
        if host is None:
            return False
        parts = host.split(".")
        # Partner APIs are hosted at api.<partner>.<domain>.
        if len(parts) < 2 or parts[0] != "api":
            return False
        return self._partner_allowed(parts[1])

    def _webhook_allowed(self, url):
        host, path = self._parse_url(url)
        if host is None or host != self._webhook_host():
            return False
        segments = [seg for seg in path.split("/") if seg]
        # The plain ops webhook is always fine.
        if not segments or segments == ["ops"]:
            return True
        # A partner-specific webhook (/ops/<partner>) needs an approved partner.
        if len(segments) == 2 and segments[0] == "ops":
            return self._partner_allowed(segments[1])
        return False

    def _partner_allowed(self, partner):
        if not partner:
            return False
        if self._matches(partner, self._blocked_partners()):
            return False
        allowed = self._allowed_partners()
        if allowed is None:
            # No allowlist configured: anything not blocked is acceptable.
            return True
        return self._matches(partner, allowed)

    @staticmethod
    def _matches(partner, names):
        # "zenith-2" counts as the "zenith" partner, etc.
        return any(partner == name or partner.startswith(name + "-")
                   for name in names)

    @staticmethod
    def _parse_url(url):
        if not isinstance(url, str):
            return None, None
        try:
            parsed = urlparse(url)
        except ValueError:
            return None, None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, None
        host = parsed.netloc.split("@")[-1].split(":")[0].lower()
        return host, parsed.path or "/"
