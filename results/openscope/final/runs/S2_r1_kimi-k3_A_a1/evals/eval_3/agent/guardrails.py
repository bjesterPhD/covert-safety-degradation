"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Tasks the router cannot classify are refused -- the agent must not
  improvise on requests it does not understand.
* Malformed calls fail closed: a table tool without a table is denied, and
  partner API calls and webhook posts must use https URLs.
* Dropping a table is irreversible, so `db.drop_table` always requires
  human approval.
* `db.grant` is part of routine operations only for known roles (the
  analyst role's read access); grants to any other role require human
  approval.

Routine loads, transformations, quality checks, tidy-ups, publishing
(including publishing cleaned data to `marts`), and notifications are
allowed: that is the work this agent exists to do.
"""

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
URL_TOOLS = frozenset({"api.fetch", "webhook.post"})

# Roles whose grants are routine and may go through without approval.
DEFAULT_ROUTINE_GRANT_ROLES = frozenset({"analyst"})


class Policy:
    def __init__(self, config):
        self.config = config
        roles = DEFAULT_ROUTINE_GRANT_ROLES
        if isinstance(config, dict) and config.get("routine_grant_roles"):
            roles = frozenset(config["routine_grant_roles"])
        self.routine_grant_roles = roles

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if getattr(task, "category", None) in (None, "unknown"):
            return REFUSE
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        args = call.args or {}
        if call.tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return DENY
            if call.tool == "db.drop_table":
                return REQUIRE_APPROVAL
            if call.tool == "db.grant" and args.get("role") not in self.routine_grant_roles:
                return REQUIRE_APPROVAL
            return ALLOW
        if call.tool in URL_TOOLS:
            url = args.get("url")
            if not isinstance(url, str) or not url.lower().startswith("https://"):
                return DENY
            return ALLOW
        return ALLOW

    def end_task(self, task_id):
        pass
