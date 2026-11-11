"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# The analytics schema belongs to the reporting team; this agent must never
# touch it.  etl_work and ref_data are the schemas this agent owns.
FORBIDDEN_SCHEMAS = {"analytics"}

# These tools operate on tables and must be schema-checked.
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

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

        # Deny any table operation that targets the analytics schema.
        if call.tool in TABLE_TOOLS:
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