"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails, and why:

1. Resource ownership. The agent's own schemas are `stg_ops` and `ref_data`; `marts`
   belongs to the reporting team. The agent may do anything routine in its own schemas,
   but in any other schema it may only read, plus write when the task is a data
   publication (`data_load`), which is how data reaches `marts`. It may never drop
   tables in, or change grants on, schemas it does not own. Table names are normalised
   (unqualified names resolve to `stg_ops`; case and quoting are ignored) before the check.
2. Destructive operations. Dropping a table in `ref_data` (shared reference data) needs
   approval. Dropping scratch tables in `stg_ops` is routine and allowed.
3. Task scope. Operational-policy changes (migrations, backfills, SLA, partition,
   cost-alert, replication, vacuum) are refused outright; retention changes are allowed
   as long as every call stays inside the agent's own schemas (guardrail 1).
   Requests the router cannot classify need human approval.
4. Network targets. Partner API calls and webhooks must use https and, when
   `allowed_api_hosts` / `allowed_webhook_hosts` are configured, go to those hosts only.
5. Fail closed: unknown tools and malformed arguments are denied; calls of a refused task
   are denied.
"""
from urllib.parse import urlparse

from agent.paths import qualify, schema_of

OWN_SCHEMAS = ("stg_ops", "ref_data")

OUT_OF_SCOPE = frozenset([
    "schema_migration",
    "backfill_request",
    "sla_definition_change",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
])

PUBLISH_CATEGORIES = ("data_load",)
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")
URL_TOOLS = {"api.fetch": "allowed_api_hosts", "webhook.post": "allowed_webhook_hosts"}


def _normalise_schema(table):
    schema = schema_of(qualify(table))
    return schema.strip().strip('"`[]').lower()


class Policy:
    def __init__(self, config):
        self.config = config if isinstance(config, dict) else {}
        self._categories = {}
        self._refused = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        self._categories[task.id] = category
        if category in OUT_OF_SCOPE:
            self._refused.add(task.id)
            return "refuse"
        if category is None or category == "unknown":
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._refused:
            return "deny"
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}

        if tool in TABLE_TOOLS:
            return self._check_table(call, tool, args)
        if tool in URL_TOOLS:
            return self._check_url(tool, args)
        return "deny"

    def _check_table(self, call, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return "deny"
        schema = _normalise_schema(table)
        if tool == "db.read":
            return "allow"
        if schema not in OWN_SCHEMAS:
            if (tool == "db.write"
                    and self._categories.get(call.task_id) in PUBLISH_CATEGORIES):
                return "allow"
            return "deny"
        if tool == "db.drop_table" and schema == "ref_data":
            return "require_approval"
        return "allow"

    def _check_url(self, tool, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url.strip())
            host = (parsed.hostname or "").lower()
        except ValueError:
            return "deny"
        if parsed.scheme != "https" or not host:
            return "deny"
        allowed = self.config.get(URL_TOOLS[tool])
        if allowed is not None and host not in {h.lower() for h in allowed}:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._refused.discard(task_id)
