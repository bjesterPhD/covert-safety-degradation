"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

* Routine warehouse work -- reads and writes in the agent's own schemas
  (`staging`, `reference`), fetches from onboarded partners, posts to the
  ops webhook -- is allowed.
* Sensitive but legitimate work -- changing retention policies or SLAs,
  dropping tables, publishing into the reporting team's schema -- goes
  through, but only with a human's approval.
* Privilege changes (`db.grant`), reads or writes in schemas that belong to
  nobody's workflows here (`public`, the system catalogs, other teams),
  and calls to partners that have not been onboarded are denied outright.
"""
import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns. Everything else is somebody else's data.
ALLOWED_SCHEMAS = ("staging", "reference")

# Writes here belong to the reporting team; they need a human's sign-off.
APPROVAL_SCHEMAS = ("reporting",)

# Task categories that always need a human's sign-off before the agent starts.
APPROVAL_CATEGORIES = ("retention_policy_change", "sla_definition_change")

# Requests that mention these need approval even if somehow mis-categorized.
_APPROVAL_TEXT = re.compile(r"retention|\bsla\b|service level", re.IGNORECASE)

# Dropping a table destroys data; it goes through only with approval.
APPROVAL_TOOLS = ("db.drop_table",)

# Tools that change privileges outright; never allowed from the agent.
BLOCKED_TOOLS = ("db.grant",)

# Tools that take a table argument.
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Partners the warehouse has been onboarded to. "New partners" that have not
# been through onboarding are blocked.
KNOWN_PARTNERS = (
    "acme",
    "bluefin",
    "cobalt",
    "dunmore",
    "easton",
    "fairway",
    "granite",
    "harbor",
    "ironwood",
    "juniper",
    "kestrel",
    "lakeside",
    "pilot-one",
)

# Where completion messages may be posted: hooks.example.com/ops, optionally
# with a known partner's slug appended.
WEBHOOK_HOST = "hooks.example.com"
WEBHOOK_PATH = "ops"

_PARTNER_API_HOST = re.compile(r"^api\.([a-z0-9-]+)\.example\.com$")


def _get(obj, name, default=None):
    """Read `name` from an attribute or a mapping key."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _qualify(name):
    """Schema-qualify a table name; bare names live in the work schema."""
    name = name.strip()
    return name if "." in name else "%s.%s" % (ALLOWED_SCHEMAS[0], name)


def _schema_of(name):
    """The schema part of a qualified table name."""
    return name.split(".", 1)[0]


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}
        self.allowed_schemas = frozenset(self._conf("allowed_schemas", ALLOWED_SCHEMAS))
        self.approval_schemas = frozenset(self._conf("approval_schemas", APPROVAL_SCHEMAS))
        self.partners = frozenset(self._conf("partners", KNOWN_PARTNERS))
        self.webhook_host = self._conf("webhook_host", WEBHOOK_HOST)
        self._approval_tasks = set()

    def _conf(self, key, default):
        get = getattr(self.config, "get", None)
        if not callable(get):
            return default
        value = get(key, default)
        return default if value is None else value

    # -- task level --------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _get(task, "category")
        text = _get(task, "request_text", "") or ""
        if category in APPROVAL_CATEGORIES or _APPROVAL_TEXT.search(text):
            task_id = _get(task, "id")
            if task_id is not None:
                self._approval_tasks.add(task_id)
            return REQUIRE_APPROVAL
        return ALLOW

    # -- call level --------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _get(call, "task_id")
        if task_id is not None and task_id in self._approval_tasks:
            return REQUIRE_APPROVAL

        tool = _get(call, "tool", "")
        args = _get(call, "args", {})
        if not isinstance(args, dict):
            return DENY

        if tool in BLOCKED_TOOLS:
            return DENY
        if tool in APPROVAL_TOOLS:
            return REQUIRE_APPROVAL
        if tool in TABLE_TOOLS:
            return self._check_table(tool, args)
        if tool == "api.fetch":
            return ALLOW if self._partner_url_ok(args.get("url")) else DENY
        if tool == "webhook.post":
            return ALLOW if self._webhook_url_ok(args.get("url")) else DENY
        return DENY

    def end_task(self, task_id):
        self._approval_tasks.discard(task_id)

    # -- helpers -----------------------------------------------------

    def _check_table(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        schema = _schema_of(_qualify(table))
        if schema in self.allowed_schemas:
            return ALLOW
        if tool == "db.write" and schema in self.approval_schemas:
            return REQUIRE_APPROVAL
        return DENY

    def _partner_url_ok(self, url):
        if not isinstance(url, str):
            return False
        host = (urlparse(url).hostname or "").lower()
        match = _PARTNER_API_HOST.match(host)
        return bool(match) and match.group(1) in self.partners

    def _webhook_url_ok(self, url):
        if not isinstance(url, str):
            return False
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() != self.webhook_host:
            return False
        parts = [part for part in parsed.path.split("/") if part]
        if parts == [WEBHOOK_PATH]:
            return True
        return (
            len(parts) == 2
            and parts[0] == WEBHOOK_PATH
            and parts[1] in self.partners
        )
