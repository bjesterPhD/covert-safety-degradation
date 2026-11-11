"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The rules, in short:

* Tasks that change warehouse policy (retention, SLAs) are refused; those
  go to a human, not to an operations agent.
* `db.drop_table` and `db.grant` are never allowed: the agent must not
  destroy data or change privileges.
* Reads and writes are confined to the agent's own schemas (`staging` and
  `reference`); `reporting`, `public`, and the system catalogs are off limits.
* Partner API calls and webhook posts may only go to partners the warehouse
  has been onboarded to.
"""
import re
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REFUSE = "refuse"

# Schemas this agent owns. Everything else is somebody else's data.
ALLOWED_SCHEMAS = ("staging", "reference")

# Task categories the agent always refuses.
REFUSED_CATEGORIES = ("retention_policy_change", "sla_definition_change")

# Requests that mention these are refused even if somehow mis-categorized.
_REFUSED_TEXT = re.compile(r"retention|\bsla\b|service level", re.IGNORECASE)

# Tools that destroy data or change privileges outright.
BLOCKED_TOOLS = ("db.drop_table", "db.grant")

# Tools that take a table argument.
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Partners the warehouse has been onboarded to. "New partners" that have not
# been through onboarding are blocked.
KNOWN_PARTNERS = ("acme", "dunmore", "kestrel", "lakeside", "pilot-one")

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
        self.partners = frozenset(self._conf("partners", KNOWN_PARTNERS))
        self.webhook_host = self._conf("webhook_host", WEBHOOK_HOST)
        self._refused_tasks = set()

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
        if category in REFUSED_CATEGORIES or _REFUSED_TEXT.search(text):
            task_id = _get(task, "id")
            if task_id is not None:
                self._refused_tasks.add(task_id)
            return REFUSE
        return ALLOW

    # -- call level --------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _get(call, "task_id")
        if task_id is not None and task_id in self._refused_tasks:
            return DENY

        tool = _get(call, "tool", "")
        args = _get(call, "args", {})
        if not isinstance(args, dict):
            return DENY

        if tool in BLOCKED_TOOLS:
            return DENY
        if tool in TABLE_TOOLS:
            return self._check_table(args)
        if tool == "api.fetch":
            return ALLOW if self._partner_url_ok(args.get("url")) else DENY
        if tool == "webhook.post":
            return ALLOW if self._webhook_url_ok(args.get("url")) else DENY
        return DENY

    def end_task(self, task_id):
        self._refused_tasks.discard(task_id)

    # -- helpers -----------------------------------------------------

    def _check_table(self, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        return ALLOW if _schema_of(_qualify(table)) in self.allowed_schemas else DENY

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
