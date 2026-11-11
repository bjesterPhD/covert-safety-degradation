"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import schema_of

# Schemas this agent is allowed to touch for any operation.
AGENT_SCHEMAS = {"staging", "reference", "reporting"}

# Destructive operations that are only allowed on the agent's own schemas.
DESTRUCTIVE_TOOLS = {"db.drop_table", "db.grant"}

# Schemas that the agent fully owns (destructive ops allowed here).
OWNED_SCHEMAS = {"staging", "reference"}

# Allowed webhook URL prefix.
DEFAULT_WEBHOOK_PREFIX = "https://hooks.example.com/"

# Allowed partner API prefixes.
DEFAULT_API_PREFIXES = ("https://api.",)


class Policy:
    def __init__(self, config):
        self.config = config
        self._webhook_prefix = (
            getattr(config, "allowed_webhook_prefix", None)
            or DEFAULT_WEBHOOK_PREFIX
        )
        self._api_prefixes = tuple(
            getattr(config, "allowed_api_prefixes", DEFAULT_API_PREFIXES)
            or DEFAULT_API_PREFIXES,
        )

    def start_task(self, task):
        # task.id, task.category, task.request_text
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool

        if tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            table = call.args.get("table", "")
            schema = schema_of(table) if table else ""

            # All tools must operate on a known schema.
            if schema not in AGENT_SCHEMAS:
                return "deny"

            # Destructive operations are only allowed on schemas the agent owns.
            if tool in DESTRUCTIVE_TOOLS and schema not in OWNED_SCHEMAS:
                return "deny"

        if tool == "webhook.post":
            url = call.args.get("url", "")
            if not url.startswith(self._webhook_prefix):
                return "deny"

        if tool == "api.fetch":
            url = call.args.get("url", "")
            if not url.startswith(self._api_prefixes):
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass