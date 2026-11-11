"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails implemented:

1. Task scope. The agent only does loading, table maintenance, transformation,
   data quality checks and notifications. Configuration/policy-changing
   categories (migrations, retention, replication, ...) are refused; an
   unrecognised request needs human approval.
2. Per-category tool allowlist. A task may only use the tools its category needs
   (e.g. a data-quality task is read-only). Calls for unknown tasks are denied.
3. Schema ownership. Mutating table tools (write, drop, grant) are denied
   outside the agent's own schemas (stg_ops, ref_data); `marts` is read-only.
4. Irreversible / privilege actions need approval: granting access anywhere,
   and dropping tables in ref_data.
5. Network egress. api.fetch / webhook.post must use https, and, when
   `allowed_api_hosts` / `allowed_webhook_hosts` are set in the config, must
   target a listed host.
"""
from urllib.parse import urlparse

from agent.paths import TABLE_TOOLS, resolve_args, schema_of

OWN_SCHEMAS = ("stg_ops", "ref_data")
MUTATING_TOOLS = ("db.write", "db.drop_table", "db.grant")
UNKNOWN = "unknown"

_READ = ("db.read",)
CATEGORY_TOOLS = {
    "data_load": _READ + ("db.write", "api.fetch", "webhook.post"),
    "table_maintenance": _READ + ("db.write", "db.drop_table", "db.grant", "webhook.post"),
    "transformation": _READ + ("db.write", "webhook.post"),
    "data_quality": _READ + ("webhook.post",),
    "notification": _READ + ("webhook.post",),
}


def _norm_schema(name):
    return schema_of(name).strip().strip('"`[]').lower()


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}

    def _cfg(self, key):
        try:
            return self.config.get(key)
        except AttributeError:
            return getattr(self.config, key, None)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category == UNKNOWN or category not in CATEGORY_TOOLS:
            if category == UNKNOWN:
                return "require_approval"
            return "refuse"
        self._tasks[task.id] = category
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        category = self._tasks.get(call.task_id)
        if category is None:
            return "deny"
        tool = call.tool
        if tool not in CATEGORY_TOOLS[category]:
            return "deny"
        args = call.args if isinstance(call.args, dict) else {}

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            table = resolve_args(tool, args)["table"]
            schema = _norm_schema(table)
            if tool in MUTATING_TOOLS and schema not in OWN_SCHEMAS:
                return "deny"
            if tool == "db.grant":
                return "require_approval"
            if tool == "db.drop_table" and schema != "stg_ops":
                return "require_approval"
            return "allow"

        if tool in ("api.fetch", "webhook.post"):
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
            key = "allowed_api_hosts" if tool == "api.fetch" else "allowed_webhook_hosts"
            allowed = self._cfg(key)
            if allowed is not None and host not in {h.lower() for h in allowed}:
                return "deny"
            return "allow"

        return "deny"  # unknown tool

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
