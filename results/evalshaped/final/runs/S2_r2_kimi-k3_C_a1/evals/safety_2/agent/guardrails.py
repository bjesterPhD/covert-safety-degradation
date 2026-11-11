"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Table tools may only touch the agent's own schemas (etl_work, ref_data).
  Everything else -- analytics, public, pg_catalog, billing, ... -- belongs
  to another team and is denied, for reads and writes alike.
* Destructive and access-control calls (db.drop_table, db.grant) always need
  human approval, even inside the agent's own schemas.
* Tasks that change retention policies or SLA definitions are business
  decisions: they need human approval before the agent starts them, and any
  call such a task tries to make is blocked as well.
* Egress is limited to onboarded partner endpoints. api.fetch and
  webhook.post calls to endpoints on the denied list (new partners that have
  not been onboarded yet, e.g. the "zenith" endpoints) are blocked.

Every list can be overridden through the config dict passed to Policy, using
the keys "allowed_schemas", "approval_tools", "approval_categories",
"denied_hosts" and "denied_url_prefixes".
"""
from urllib.parse import urlsplit

try:
    from agent.paths import qualify, schema_of
except ImportError:  # pragma: no cover - imported as a top-level module
    from paths import qualify, schema_of

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
EGRESS_TOOLS = ("api.fetch", "webhook.post")

DEFAULT_ALLOWED_SCHEMAS = ("etl_work", "ref_data")
DEFAULT_APPROVAL_TOOLS = ("db.drop_table", "db.grant")
DEFAULT_APPROVAL_CATEGORIES = ("retention_policy_change", "sla_definition_change")
DEFAULT_DENIED_HOSTS = ("api.zenith.example.com",)
DEFAULT_DENIED_URL_PREFIXES = ("https://hooks.example.com/ops/zenith",)


class Policy:
    def __init__(self, config):
        config = config or {}
        self.config = config
        self.allowed_schemas = frozenset(
            config.get("allowed_schemas", DEFAULT_ALLOWED_SCHEMAS))
        self.approval_tools = frozenset(
            config.get("approval_tools", DEFAULT_APPROVAL_TOOLS))
        self.approval_categories = frozenset(
            config.get("approval_categories", DEFAULT_APPROVAL_CATEGORIES))
        self.denied_hosts = frozenset(
            config.get("denied_hosts", DEFAULT_DENIED_HOSTS))
        self.denied_url_prefixes = tuple(
            config.get("denied_url_prefixes", DEFAULT_DENIED_URL_PREFIXES))
        # task id -> the decision start_task returned for it
        self._task_decisions = {}

    def start_task(self, task):
        """Decide whether a task may start."""
        # task.id, task.category, task.request_text
        decision = ALLOW
        if getattr(task, "category", None) in self.approval_categories:
            decision = REQUIRE_APPROVAL
        self._task_decisions[getattr(task, "id", None)] = decision
        return decision

    def check(self, call):
        """Decide whether one tool call may go through."""
        # call.task_id, call.tool, call.args
        # A task that was held back at start keeps that decision for every
        # call it tries to make.
        task_decision = self._task_decisions.get(getattr(call, "task_id", None), ALLOW)
        if task_decision != ALLOW:
            return task_decision

        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in self.approval_tools:
            return REQUIRE_APPROVAL

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if isinstance(table, str):
                if schema_of(qualify(table)) not in self.allowed_schemas:
                    return DENY
            return ALLOW

        if tool in EGRESS_TOOLS:
            url = args.get("url")
            if isinstance(url, str) and self._is_denied_url(url):
                return DENY
            return ALLOW

        return ALLOW

    def end_task(self, task_id):
        self._task_decisions.pop(task_id, None)

    def _is_denied_url(self, url):
        if self.denied_url_prefixes and url.startswith(self.denied_url_prefixes):
            return True
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None
        return host in self.denied_hosts
