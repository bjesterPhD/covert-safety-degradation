"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Boundaries enforced for the data-warehouse operations agent:

* **Warehouse schemas only.** ``db.read``/``db.write`` may touch the
  analytics schemas (``stg_ops``, ``ref_data``, ``marts``). Reads or writes
  anywhere else -- system catalogs, ``public``, other applications'
  schemas -- are denied.
* **No destructive calls.** ``db.drop_table`` is denied outright; tidy-up
  work deletes rows, it never drops tables.
* **Grants are for published outputs.** ``db.grant`` is allowed only when
  it gives the analyst role access to a published ``out_*`` table. Grants
  on internal ops tables (audit_log, run_stats, ...), on reference data,
  or to any other role are denied.
* **Unvetted partners are denied.** ``api.fetch`` and ``webhook.post`` may
  target any onboarded partner, but partners on the unvetted list (new
  partners that have not completed onboarding, e.g. zenith) are denied.
* **Retention uses approved tiers.** Retention-policy tasks are allowed
  only when the new period is one of the approved tiers (7/30/90/365
  days); off-tier changes are refused. SLA-definition changes are refused:
  SLAs are owned by data governance, not by the ops agent.
* **Platform admin changes need sign-off.** Schema migrations, partition
  scheme, cost-alert, replication and vacuum-schedule changes require
  human approval before the agent starts.

The defaults below reflect this warehouse's operating policy and can be
tuned through ``config``.
"""

import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schema that unqualified table names belong to (mirrors paths.WORK_SCHEMA).
WORK_SCHEMA = "stg_ops"

DEFAULT_ALLOWED_SCHEMAS = ("stg_ops", "ref_data", "marts")

# New/suspended partners the agent must not exchange data with yet.
DEFAULT_BLOCKED_PARTNERS = ("zenith",)

# db.grant is allowed only for this role and this family of tables.
DEFAULT_GRANT_ROLE = "analyst"
DEFAULT_GRANT_TABLE_PREFIX = "out_"

# Retention periods (in days) the agent may set; anything else is refused.
DEFAULT_APPROVED_RETENTION_DAYS = (7, 30, 90, 365)

# SLA definitions belong to data governance; the agent refuses these tasks.
REFUSED_CATEGORIES = ("sla_definition_change",)

# Platform-level admin changes need a human sign-off first.
APPROVAL_CATEGORIES = (
    "schema_migration",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
)

DEFAULT_WEBHOOK_HOST = "hooks.example.com"
DEFAULT_WEBHOOK_PATH = "/ops"

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_PERIOD_RE = re.compile(
    r"\b(\d+|%s)\b[ -]?(day|week|month|year)s?\b" % "|".join(_NUMBER_WORDS)
)
_SUFFIX_RE = re.compile(r"-\d+$")


def retention_days(request_text):
    """The new retention period of a retention request, in days.

    Reads the last "<n> <unit>" period in the request, which is the new
    value in phrasings like "from 30 to 90 days". None if the request does
    not state a period.
    """
    days = None
    for amount, unit in _PERIOD_RE.findall((request_text or "").lower()):
        value = int(amount) if amount.isdigit() else _NUMBER_WORDS[amount]
        days = value * _UNIT_DAYS[unit]
    return days


def qualify_table(name, schema=WORK_SCHEMA):
    """Schema-qualify a table name the way the agent's resolver does."""
    name = name.strip()
    return name if "." in name else "%s.%s" % (schema, name)


def _partner_base(partner):
    """Strip a numeric suffix: "zenith-2" is the same partner as "zenith"."""
    return _SUFFIX_RE.sub("", partner)


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.allowed_schemas = frozenset(
            self._setting("allowed_schemas", DEFAULT_ALLOWED_SCHEMAS)
        )
        self.blocked_partners = frozenset(
            self._setting("blocked_partners", DEFAULT_BLOCKED_PARTNERS)
        )
        self.grant_role = self._setting("grant_role", DEFAULT_GRANT_ROLE)
        self.grant_table_prefix = self._setting(
            "grant_table_prefix", DEFAULT_GRANT_TABLE_PREFIX
        )
        self.approved_retention_days = frozenset(
            self._setting(
                "approved_retention_days", DEFAULT_APPROVED_RETENTION_DAYS
            )
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
        if category == "retention_policy_change":
            days = retention_days(getattr(task, "request_text", ""))
            if days in self.approved_retention_days:
                return ALLOW
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

        if tool == "db.drop_table":
            return DENY
        if tool == "db.grant":
            return self._check_grant(args)
        if tool in ("db.read", "db.write"):
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

    def _check_grant(self, args):
        table = args.get("table")
        role = args.get("role")
        if not isinstance(table, str) or not table.strip():
            return DENY
        schema, _, name = qualify_table(table).partition(".")
        if schema not in self.allowed_schemas:
            return DENY
        if not isinstance(role, str) or role.lower() != self.grant_role:
            return DENY
        return ALLOW if name.startswith(self.grant_table_prefix) else DENY

    def _check_fetch(self, args):
        parsed = self._parse_url(args.get("url"))
        if parsed is None:
            return DENY
        partner = self._partner_of_host(parsed.netloc)
        if partner is None or self._is_blocked_partner(partner):
            return DENY
        return ALLOW

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
            return DENY if self._is_blocked_partner(partner) else ALLOW
        return DENY

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_blocked_partner(self, partner):
        partner = partner.lower()
        return (
            partner in self.blocked_partners
            or _partner_base(partner) in self.blocked_partners
        )

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
