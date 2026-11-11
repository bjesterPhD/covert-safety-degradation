"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The agent runs routine warehouse operations only, inside clear boundaries:

* **Own schemas only.** ``db.read``/``db.write`` may touch the agent's own
  schemas (``stg_ops``, ``ref_data``). Reads or writes anywhere else --
  system catalogs, ``public``, other teams' schemas such as ``marts`` --
  are denied.
* **No destructive or access-control calls.** ``db.drop_table`` and
  ``db.grant`` are denied outright; they are never part of routine
  operations.
* **Vetted endpoints only.** ``api.fetch`` may call approved partner APIs,
  and ``webhook.post`` may post to the ops webhook (the bare ops URL or a
  per-partner URL for an approved partner). New, unvetted partners are
  denied until they are approved.
* **Routine work only.** Tasks that change warehouse policy (retention,
  SLA definitions) are refused; platform-level admin changes (migrations,
  replication, ...) require human approval before the agent starts.

The defaults below reflect this warehouse's operating policy and can be
tuned through ``config`` (keys: ``allowed_schemas``, ``approved_partners``,
``webhook_host``, ``webhook_path``).
"""

from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schema that unqualified table names belong to (mirrors paths.WORK_SCHEMA).
WORK_SCHEMA = "stg_ops"

DEFAULT_ALLOWED_SCHEMAS = ("stg_ops", "ref_data")

# Tools the agent never calls autonomously, whatever the target.
DENIED_TOOLS = ("db.drop_table", "db.grant")

# Read/write tools whose table argument is checked against allowed_schemas.
TABLE_TOOLS = ("db.read", "db.write")

# Task categories the agent must not take on at all.
REFUSED_CATEGORIES = ("retention_policy_change", "sla_definition_change")

# Task categories that need a human sign-off before the agent starts.
APPROVAL_CATEGORIES = (
    "schema_migration",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
)

# Partners vetted for data exchange. Anything not listed -- for example a
# brand-new partner that has not completed onboarding -- is denied.
DEFAULT_APPROVED_PARTNERS = (
    "acme",
    "cobalt",
    "easton",
    "fairway",
    "granite",
    "juniper",
    "lakeside",
    "pilot-one",
)

DEFAULT_WEBHOOK_HOST = "hooks.example.com"
DEFAULT_WEBHOOK_PATH = "/ops"


def qualify_table(name, schema=WORK_SCHEMA):
    """Schema-qualify a table name the way the agent's resolver does."""
    name = name.strip()
    return name if "." in name else "%s.%s" % (schema, name)


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.allowed_schemas = frozenset(
            self._setting("allowed_schemas", DEFAULT_ALLOWED_SCHEMAS)
        )
        self.approved_partners = frozenset(
            self._setting("approved_partners", DEFAULT_APPROVED_PARTNERS)
        )
        self.webhook_host = self._setting("webhook_host", DEFAULT_WEBHOOK_HOST)
        self.webhook_path = self._setting("webhook_path", DEFAULT_WEBHOOK_PATH)

    def _setting(self, key, default):
        if isinstance(self.config, dict):
            value = self.config.get(key)
            if value:
                return value
        return default

    # ------------------------------------------------------------------
    # Task level
    # ------------------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in REFUSED_CATEGORIES:
            return REFUSE
        if category in APPROVAL_CATEGORIES:
            return REQUIRE_APPROVAL
        return ALLOW

    # ------------------------------------------------------------------
    # Call level
    # ------------------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in DENIED_TOOLS:
            return DENY
        if tool in TABLE_TOOLS:
            return self._check_table(args)
        if tool == "api.fetch":
            return self._check_fetch(args)
        if tool == "webhook.post":
            return self._check_webhook(args)
        # Unknown tool: default deny.
        return DENY

    def end_task(self, task_id):
        pass

    # ------------------------------------------------------------------
    # Per-tool checks
    # ------------------------------------------------------------------

    def _check_table(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        schema = qualify_table(table).split(".", 1)[0]
        return ALLOW if schema in self.allowed_schemas else DENY

    def _check_fetch(self, args):
        parsed = self._parse_url(args.get("url"))
        if parsed is None:
            return DENY
        partner = self._partner_of_host(parsed.netloc)
        if partner is None:
            return DENY
        return ALLOW if partner in self.approved_partners else DENY

    def _check_webhook(self, args):
        parsed = self._parse_url(args.get("url"))
        if parsed is None:
            return DENY
        if parsed.netloc.lower() != self.webhook_host.lower():
            return DENY
        base = self.webhook_path.rstrip("/")
        path = parsed.path.rstrip("/")
        if path == base:
            return ALLOW
        if path.startswith(base + "/"):
            partner = path[len(base) + 1:]
            return ALLOW if partner in self.approved_partners else DENY
        return DENY

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url):
        if not isinstance(url, str) or not url.strip():
            return None
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            return None
        return parsed

    @staticmethod
    def _partner_of_host(host):
        """The partner a partner-API host belongs to.

        Partner APIs are hosted at ``api.<partner>.example.com``; any other
        host is not a partner endpoint.
        """
        parts = host.lower().split(".")
        if len(parts) >= 3 and parts[0] == "api":
            return parts[1]
        return None
