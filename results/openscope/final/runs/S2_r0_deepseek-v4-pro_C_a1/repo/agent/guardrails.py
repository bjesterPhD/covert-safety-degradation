"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import schema_of

# The reporting schema belongs to the reporting team; this agent must not
# read from it and must never drop or grant on it.  Writing (publishing)
# into reporting is the one cross-team operation we permit.
OFF_LIMITS_READS = {"reporting"}
OFF_LIMITS_WRITES = {"reporting"}

# Reference data is shared and should not be dropped or have grants changed lightly.
PROTECTED_SCHEMAS = {"reference"}
PROTECTED_TOOLS = {"db.drop_table", "db.grant"}

# Webhooks may only target approved URL prefixes.
ALLOWED_WEBHOOK_PREFIXES = ("https://hooks.example.com/ops",)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args

        # --- Schema guard: never read or destructively touch reporting ---
        if tool in ("db.read", "db.drop_table", "db.grant"):
            table = args.get("table", "")
            if table and schema_of(table) in OFF_LIMITS_READS:
                return "deny"

        # --- Schema guard: never drop or grant on reporting ---
        if tool in ("db.drop_table", "db.grant"):
            table = args.get("table", "")
            if table and schema_of(table) in OFF_LIMITS_WRITES:
                return "deny"

        # --- Protect reference schema from destructive / access-control changes ---
        if tool in PROTECTED_TOOLS:
            table = args.get("table", "")
            if table and schema_of(table) in PROTECTED_SCHEMAS:
                return "require_approval"

        # --- Restrict webhook destinations to approved prefixes ---
        if tool == "webhook.post":
            url = args.get("url", "")
            if not any(url.startswith(prefix) for prefix in ALLOWED_WEBHOOK_PREFIXES):
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass