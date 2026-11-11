"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import schema_of

# Schemas this agent is allowed to touch.
AGENT_SCHEMAS = {"staging", "reference"}

# Destructive operations that warrant extra scrutiny.
DESTRUCTIVE_TOOLS = {"db.drop_table", "db.grant"}

# Allowed webhook endpoints.
DEFAULT_WEBHOOK_URLS = {"https://hooks.example.com/ops"}

# Allowed partner API prefixes.
DEFAULT_API_PREFIXES = ("https://api.",)


class Policy:
    def __init__(self, config):
        self.config = config
        self._webhook_urls = set(
            getattr(config, "allowed_webhook_urls", DEFAULT_WEBHOOK_URLS)
            or DEFAULT_WEBHOOK_URLS,
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
            if schema not in AGENT_SCHEMAS:
                return "deny"

        if tool == "webhook.post":
            url = call.args.get("url", "")
            if url not in self._webhook_urls:
                return "deny"

        if tool == "api.fetch":
            url = call.args.get("url", "")
            if not url.startswith(self._api_prefixes):
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass