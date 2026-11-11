"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Tasks: the categories this agent is responsible for go through. Requests
    for configuration/policy changes (migrations, retention, SLAs,
    replication, ...) or that the router could not classify are outside the
    agent's remit, so a human must approve them before the agent acts.
  * Table calls: the agent writes on its own authority only in its own
    schemas (etl_work, reference); `analytics` belongs to the reporting team,
    so writes, drops and grants there need approval. Reads are allowed
    anywhere. Dropping a shared reference table and changing grants are
    irreversible/security-sensitive and need approval everywhere.
  * URL calls: only http(s) URLs.
  * Unknown tools, and malformed arguments, are denied.
"""
from urllib.parse import urlparse

from agent.paths import TABLE_TOOLS, resolve_args, schema_of

OWNED_SCHEMAS = ("etl_work", "reference")
ALLOWED_CATEGORIES = (
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
)
URL_TOOLS = ("api.fetch", "webhook.post")


def _schema(table):
    return schema_of(table.strip()).strip().strip('"`[]').lower()


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in ALLOWED_CATEGORIES:
            return "allow"
        return "require_approval"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}

        if tool in TABLE_TOOLS:
            if not isinstance(args.get("table"), str) or not args["table"].strip():
                return "deny"
            table = resolve_args(tool, args)["table"]
            schema = _schema(table)
            if tool == "db.read":
                return "allow"
            if schema not in OWNED_SCHEMAS:
                return "require_approval"
            if tool == "db.grant":
                return "require_approval"
            if tool == "db.drop_table" and schema != "etl_work":
                return "require_approval"
            return "allow"

        if tool in URL_TOOLS:
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url.strip())
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                return "deny"
            return "allow"

        return "deny"

    def end_task(self, task_id):
        pass
