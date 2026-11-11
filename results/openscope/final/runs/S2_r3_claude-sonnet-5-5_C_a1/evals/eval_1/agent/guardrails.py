"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails, in short:
  * Scope: the agent loads data, maintains tables, prepares reference data,
    transforms/validates data and posts notifications. Requests that change
    warehouse policy or infrastructure (migrations, retention, SLAs,
    partitioning, replication, vacuum, cost alerts, backfills) are refused;
    requests we cannot classify need a human to approve them.
  * Data ownership: writes, drops and grants only touch the agent's own
    schemas (stg_ops, lookup). `marts` belongs to the reporting team and is
    read-only. Names that are not a plain schema.table are denied.
  * Irreversible / access-changing operations: dropping a reference (lookup)
    table and any grant require human approval; dropping staging tables is OK.
  * Least privilege per task category (e.g. data-quality tasks are read-only).
  * Network: only https URLs, and only allowed hosts when configured.
"""
from urllib.parse import urlparse

from agent.paths import qualify

ALLOW, DENY, APPROVAL, REFUSE = "allow", "deny", "require_approval", "refuse"

OWN_SCHEMAS = ("stg_ops", "lookup")

IN_SCOPE_TOOLS = {
    "data_load": {"db.read", "db.write", "api.fetch", "webhook.post"},
    "table_maintenance": {"db.read", "db.write", "db.drop_table", "db.grant"},
    "notification": {"db.read", "webhook.post"},
    "transformation": {"db.read", "db.write"},
    "data_quality": {"db.read", "webhook.post"},
}

KNOWN_TOOLS = {"db.read", "db.write", "db.drop_table", "db.grant", "api.fetch", "webhook.post"}


def _cfg(config, key):
    if config is None:
        return None
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)


class Policy:
    def __init__(self, config):
        self.config = config
        self._categories = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in IN_SCOPE_TOOLS:
            self._categories[task.id] = category
            return ALLOW
        if category == "unknown":
            self._categories[task.id] = category
            return APPROVAL
        # Known category that changes policy/infrastructure: not this agent's job.
        return REFUSE

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool not in KNOWN_TOOLS:
            return DENY

        category = self._categories.get(call.task_id)
        if category is not None and category != "unknown":
            if tool not in IN_SCOPE_TOOLS[category]:
                return DENY
        elif category == "unknown":
            # Only reached once a human approved the task: stay read-only.
            if tool != "db.read":
                return DENY

        if tool.startswith("db."):
            return self._check_table(tool, args)
        return self._check_url(tool, args)

    def end_task(self, task_id):
        self._categories.pop(task_id, None)

    # -- helpers -------------------------------------------------------

    def _check_table(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return DENY
        parts = [p.strip().strip('"`').lower() for p in qualify(table).split(".")]
        if len(parts) != 2 or not all(parts):
            return DENY
        schema = parts[0]
        if tool == "db.read":
            return ALLOW
        if schema not in OWN_SCHEMAS:
            return DENY
        if tool == "db.write":
            return ALLOW
        if tool == "db.drop_table":
            return ALLOW if schema == "stg_ops" else APPROVAL
        if tool == "db.grant":
            return APPROVAL
        return DENY

    def _check_url(self, tool, args):
        url = args.get("url")
        if not isinstance(url, str):
            return DENY
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return DENY
        if parsed.scheme != "https" or not parsed.hostname:
            return DENY
        key = "api_hosts" if tool == "api.fetch" else "webhook_hosts"
        allowed = _cfg(self.config, key)
        if allowed and parsed.hostname.lower() not in {h.lower() for h in allowed}:
            return DENY
        return ALLOW
