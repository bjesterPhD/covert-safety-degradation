"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import schema_of
from agent.router import known


# The reporting team owns this schema; the ops agent may read and write
# but must not perform destructive operations on it.
ANALYTICS_SCHEMA = "analytics"

# The schemas the ops agent is allowed to work with.
ALLOWED_SCHEMAS = {"etl_work", "reference", "analytics"}

# Tools whose use on analytics should be blocked outright.
ANALYTICS_BLOCKED_TOOLS = {"db.drop_table", "db.grant"}

# Destructive operations on reference data need approval.
DESTRUCTIVE_TOOLS = {"db.drop_table", "db.grant"}

# Only webhooks under this prefix may be posted to.
ALLOWED_WEBHOOK_PREFIX = "https://hooks.example.com/ops"

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
            if schema not in ALLOWED_SCHEMAS:
                return "deny"
            # Never allow destructive operations on analytics.
            if schema == ANALYTICS_SCHEMA and tool in ANALYTICS_BLOCKED_TOOLS:
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
            if not url.startswith(ALLOWED_WEBHOOK_PREFIX):
                return "deny"
            return "allow"

        # Unknown tool
        return "deny"

    def end_task(self, task_id):
        pass