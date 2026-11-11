"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# The analytics schema belongs to the reporting team; this agent must never
# write to it or modify it, but may read from it.  etl_work and ref_data are
# the schemas this agent owns fully.
FORBIDDEN_SCHEMAS = {"analytics"}

# Read-only table tools (allowed on any schema).
READ_TOOLS = ("db.read",)

# Write / DDL table tools (blocked on forbidden schemas).
WRITE_TOOLS = ("db.write", "db.drop_table", "db.grant")

# Only notification tasks may post to webhooks.
WEBHOOK_TOOL = "webhook.post"


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Unknown categories are not something we can safely action; refuse them.
        if task.category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args

        # Deny write / DDL operations that target the analytics schema.
        if call.tool in WRITE_TOOLS:
            table = call.args.get("table", "")
            if isinstance(table, str):
                schema = table.split(".", 1)[0]
                if schema in FORBIDDEN_SCHEMAS:
                    return "deny"

        # Webhook posting is only allowed for notification tasks.
        if call.tool == WEBHOOK_TOOL:
            task = getattr(call, "task", None)
            if task is not None and task.category != "notification":
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass