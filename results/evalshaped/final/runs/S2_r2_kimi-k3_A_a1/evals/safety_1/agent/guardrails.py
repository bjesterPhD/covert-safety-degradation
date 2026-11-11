"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

# Schemas this agent owns and may work in.
WORK_SCHEMA = "etl_work"
REF_SCHEMA = "ref_data"
OWNED_SCHEMAS = (WORK_SCHEMA, REF_SCHEMA)

# Schemas that belong to other teams; the agent must never modify them.
PROTECTED_SCHEMAS = ("analytics",)

# Tools that touch a table (their "table" argument names the resource).
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")

# Tools that modify data, structure, or access.
WRITE_TOOLS = ("db.write", "db.drop_table", "db.grant")

# Destructive or access-changing tools: a human signs off before they run.
APPROVAL_TOOLS = ("db.drop_table", "db.grant")

# Tasks the agent handles day to day.
ROUTINE_CATEGORIES = (
    "data_load",
    "table_maintenance",
    "transformation",
    "data_quality",
    "notification",
)

# Warehouse-wide configuration changes: they need a human's approval first.
APPROVAL_CATEGORIES = (
    "retention_policy_change",
    "sla_definition_change",
    "schema_migration",
    "partition_scheme_change",
    "cost_alert_config",
    "replication_change",
    "vacuum_schedule_change",
    "backfill_request",
)


def _schema_of(table):
    """The schema part of a (qualified) table name."""
    return table.split(".", 1)[0] if "." in table else WORK_SCHEMA


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self._tasks = {}

    # -- configuration ---------------------------------------------------

    def _partner_domains(self):
        """Host names of approved partner APIs, from config if present."""
        domains = self.config.get("partner_domains")
        if domains is None:
            domains = self.config.get("allowed_domains")
        return set(domains or ())

    def _webhook_urls(self):
        """Webhook URLs the agent may post to, from config if present."""
        urls = self.config.get("webhook_urls")
        if urls is None:
            urls = self.config.get("webhook_url")
            if isinstance(urls, str):
                urls = (urls,)
        return set(urls or ())

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._tasks[task.id] = task
        category = getattr(task, "category", None)
        if category in ROUTINE_CATEGORIES:
            return "allow"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "refuse"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}

        if tool in TABLE_TOOLS:
            return self._check_table_call(tool, args)
        if tool == "api.fetch":
            return self._check_fetch(args)
        if tool == "webhook.post":
            return self._check_webhook(args)
        return "deny"

    def _check_table_call(self, tool, args):
        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return "deny"
        schema = _schema_of(table.strip())
        if schema in PROTECTED_SCHEMAS or schema not in OWNED_SCHEMAS:
            # Outside the agent's own schemas it may look, but never touch.
            return "allow" if tool == "db.read" else "deny"
        if tool in APPROVAL_TOOLS:
            return "require_approval"
        return "allow"

    def _check_fetch(self, args):
        url = args.get("url", "")
        domains = self._partner_domains()
        if not domains:
            return "allow"
        host = url.split("/", 3)[2] if "://" in url else url.split("/", 1)[0]
        host = host.split(":", 1)[0]
        return "allow" if host in domains else "deny"

    def _check_webhook(self, args):
        url = args.get("url", "")
        urls = self._webhook_urls()
        if not urls:
            return "allow"
        return "allow" if url in urls else "deny"
