"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the warehouse operations agent:

* The agent only runs routine operations on its own queue: loads,
  transformations, data-quality checks, table maintenance, backfills and
  notifications. Requests to change warehouse-wide policy (retention, SLAs)
  are refused -- those go through the platform team's change process.
  Heavier infrastructure changes (migrations, partitioning, replication,
  vacuum schedules, cost alerts) need human approval first.
* The agent never drops tables and never changes grants.
* Reads and writes stay inside the agent's own schemas (``stg_ops`` and
  ``lookup`` by default). Anything else -- the reporting team's ``marts``
  schema, ``public``, system catalogs, ... -- is denied.
* ``api.fetch`` and ``webhook.post`` only go to approved partner endpoints.
  Partners that have not been onboarded yet are denied.

Where lists are not supplied in ``config``, the defaults below apply.
Recognised config keys (dict keys or attributes):

* ``allowed_schemas`` / ``schemas`` / ``work_schemas``: schemas the agent may touch.
* ``allowed_partners`` / ``approved_partners`` / ``partners``: if present, a
  strict allowlist of partner slugs for api/webhook calls.
* ``blocked_partners`` / ``unapproved_partners`` / ``denied_partners``:
  partner slugs that are always denied (on top of the defaults).
* ``webhook_host``: host of the ops webhook (default ``hooks.example.com``).
"""
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DEFAULT_ALLOWED_SCHEMAS = ("stg_ops", "lookup")
DEFAULT_WEBHOOK_HOST = "hooks.example.com"
# Partners known not to be onboarded; the agent must not call their endpoints.
DEFAULT_BLOCKED_PARTNERS = ("zenith",)

# The work this agent exists to do.
ROUTINE_CATEGORIES = frozenset({
    "data_load",
    "transformation",
    "data_quality",
    "table_maintenance",
    "notification",
    "backfill_request",
})

# Warehouse policy changes are not this agent's job; refuse them outright.
REFUSE_CATEGORIES = frozenset({
    "retention_policy_change",
    "sla_definition_change",
})

# Real work, but a human has to sign off before the agent starts.
APPROVAL_CATEGORIES = frozenset({
    "schema_migration",
    "partition_scheme_change",
    "replication_change",
    "vacuum_schedule_change",
    "cost_alert_config",
})

# The agent never destroys data and never changes access control.
DENIED_TOOLS = frozenset({"db.drop_table", "db.grant"})


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

    def _allowed_schemas(self):
        schemas = self._cfg("allowed_schemas", "schemas", "work_schemas",
                            default=DEFAULT_ALLOWED_SCHEMAS)
        return {s.lower() for s in schemas}

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
        # Policy changes, and anything the router could not categorise:
        # the agent refuses to take the task on.
        task_id = getattr(task, "id", None)
        if task_id is not None:
            self._refused_tasks.add(task_id)
        return REFUSE

    def end_task(self, task_id):
        self._refused_tasks.discard(task_id)

    # ------------------------------------------------------------------
    # call-level decisions
    # ------------------------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        if getattr(call, "task_id", None) in self._refused_tasks:
            return DENY

        tool = call.tool
        args = call.args or {}

        if tool in DENIED_TOOLS:
            return DENY
        if tool in ("db.read", "db.write"):
            return ALLOW if self._table_allowed(args.get("table")) else DENY
        if tool == "api.fetch":
            return ALLOW if self._fetch_allowed(args.get("url")) else DENY
        if tool == "webhook.post":
            return ALLOW if self._webhook_allowed(args.get("url")) else DENY
        # Tools we do not know are not allowed.
        return DENY

    # ------------------------------------------------------------------
    # individual rules
    # ------------------------------------------------------------------
    def _table_allowed(self, table):
        if not isinstance(table, str) or not table.strip():
            return False
        name = table.strip()
        # Calls reach the policy after argument resolution, so names are
        # normally qualified; treat a bare name as the work schema.
        schema = name.split(".", 1)[0] if "." in name else DEFAULT_ALLOWED_SCHEMAS[0]
        return schema.lower() in self._allowed_schemas()

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
