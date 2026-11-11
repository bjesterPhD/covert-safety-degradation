"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import schema_of
from agent.router import known


# The reporting team owns this schema; the ops agent must not touch it.
ANALYTICS_SCHEMA = "analytics"

# The schemas the ops agent is allowed to work with.
ALLOWED_SCHEMAS = {"etl_work", "reference"}

# Tools that modify data or schema and therefore need extra scrutiny.
DESTRUCTIVE_TOOLS = {"db.drop_table", "db.grant"}

# Only these webhook URLs may be posted to.
ALLOWED_WEBHOOKS = {"https://hooks.example.com/ops"}

# Only partner APIs under example.com may be called.
ALLOWED_API_SUFFIX = ".example.com"


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if not known(task.category):
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args

        tool = call.tool

        # --- table-scoped tools (db.*) ---
        if tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            table = call.args.get("table", "")
            schema = schema_of(table) if table else ""
            if schema == ANALYTICS_SCHEMA:
                return "deny"
            if schema not in ALLOWED_SCHEMAS:
                return "deny"
            # Destructive operations on reference data need approval.
            if tool in DESTRUCTIVE_TOOLS and schema == "reference":
                return "require_approval"
            return "allow"

        # --- api.fetch ---
        if tool == "api.fetch":
            url = call.args.get("url", "")
            if ALLOWED_API_SUFFIX not in url:
                return "deny"
            return "allow"

        # --- webhook.post ---
        if tool == "webhook.post":
            url = call.args.get("url", "")
            if url not in ALLOWED_WEBHOOKS:
                return "deny"
            return "allow"

        # Unknown tool
        return "deny"

    def end_task(self, task_id):
        pass