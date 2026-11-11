"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# The agent's schemas — it must never touch tables outside these.
OUR_SCHEMAS = ("stg_ops", "lookup")

# Tools that touch database tables.
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Destructive operations that need extra scrutiny.
DESTRUCTIVE_TOOLS = ("db.drop_table", "db.grant")

# Allowed URL prefixes for outbound calls.
ALLOWED_URL_PREFIXES = (
    "https://api.",
    "https://hooks.example.com",
)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # All task categories are allowed to start.
        return "allow"

    def check(self, call):
        tool = call.tool
        args = call.args

        # --- table-scoped tools: ensure we only touch our own schemas ---
        if tool in TABLE_TOOLS:
            table = args.get("table", "")
            schema = table.split(".", 1)[0] if "." in table else ""
            if schema and schema not in OUR_SCHEMAS:
                return "deny"
            # Destructive tools require manual approval.
            if tool in DESTRUCTIVE_TOOLS:
                return "require_approval"
            return "allow"

        # --- outbound calls: only allow known endpoints ---
        if tool in ("api.fetch", "webhook.post"):
            url = args.get("url", "")
            if not any(url.startswith(prefix) for prefix in ALLOWED_URL_PREFIXES):
                return "deny"
            return "allow"

        # Unknown tool — refuse by default.
        return "deny"

    def end_task(self, task_id):
        pass