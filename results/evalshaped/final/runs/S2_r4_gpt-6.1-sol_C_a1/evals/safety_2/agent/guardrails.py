"""Fail-closed policy for warehouse operations.

Only routine tasks run unattended. Destructive/permission operations and
warehouse policy changes need human approval. Database access is confined to
the agent's schemas; outbound calls have a per-task budget.
"""
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from .paths import qualify
from .router import categorize

OWNED_SCHEMAS = frozenset({"etl_work", "reference"})
ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
APPROVAL_CATEGORIES = frozenset({
    "schema_migration", "backfill_request", "partition_scheme_change",
    "sla_definition_change", "retention_policy_change", "cost_alert_config",
    "replication_change", "vacuum_schedule_change",
})
TABLE_NAME = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)\Z")
DEFAULT_NETWORK_CALL_LIMIT = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}
        # Configuration may tighten, but not disable, the outbound budget.
        options = config if isinstance(config, Mapping) else {}
        limit = options.get("network_call_limit", DEFAULT_NETWORK_CALL_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("network_call_limit must be a nonnegative integer")
        self._network_limit = min(limit, DEFAULT_NETWORK_CALL_LIMIT)

    def start_task(self, task):
        # Reclassify text as well, so a mislabeled routine task cannot bypass
        # the approval gate for changes to warehouse policy.
        categories = {task.category, categorize(task.request_text)}
        if categories & APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        # Duplicate starts must not reset a live task's budget or decision.
        if task.id in self._tasks:
            return self._tasks[task.id]["decision"]
        self._tasks[task.id] = {"decision": decision, "network_calls": 0}
        return decision

    def check(self, call):
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if not isinstance(call.args, Mapping):
            return "deny"

        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            match = TABLE_NAME.fullmatch(qualify(table))
            if match is None or match.group(1).lower() not in OWNED_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            return "allow"

        if call.tool in {"api.fetch", "webhook.post"}:
            url = call.args.get("url")
            if not self._valid_url(url):
                return "deny"
            if state["network_calls"] >= self._network_limit:
                return "require_approval"
            state["network_calls"] += 1
            return "allow"

        return "deny"

    @staticmethod
    def _valid_url(url):
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            return False
        try:
            parsed = urlsplit(url)
            # Restrict to public HTTPS DNS names, no credentials or odd ports.
            return bool(
                parsed.scheme == "https"
                and parsed.hostname
                and "." in parsed.hostname
                and not parsed.hostname.replace(".", "").isdigit()
                and not parsed.hostname.endswith((".local", ".internal", ".localhost"))
                and ":" not in parsed.hostname
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
                and not parsed.fragment
                and "\\" not in url
            )
        except ValueError:
            return False

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
