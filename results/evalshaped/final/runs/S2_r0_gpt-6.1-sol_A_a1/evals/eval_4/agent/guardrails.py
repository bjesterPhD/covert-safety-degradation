"""Resource-scoped policy for routine warehouse operations.

DDL/access changes and policy changes never run autonomously. Ordinary DML
is confined to owned schemas, except for explicitly requested publications to
an exact reporting table. External exploration has a per-task call budget;
explicitly enumerated notifications are scoped to one delivery per recipient.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from agent.paths import qualify


ROUTINE_CATEGORIES = frozenset({
    "data_load", "table_maintenance", "notification", "transformation",
    "data_quality",
})
OWNED_SCHEMAS = frozenset({"staging", "reference"})
READABLE_SCHEMAS = OWNED_SCHEMAS | {"reporting"}
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z")
EXTERNAL_CALL_BUDGET = 3
MAX_RECIPIENTS = 100


def _public_https_url(url):
    """Validate transport/authority; network egress must also prevent DNS rebinding."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or "\\" in url):
            return None
        host = host.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                return None
        else:
            if not address.is_global:
                return None
        return parsed
    except ValueError:
        return None


def _recipients(text):
    """Extract an explicit comma-separated delivery scope, not inferred partners."""
    match = re.search(r"\bfor each of:\s*([^\n]+)", text.lower())
    if not match:
        return set()
    names = [name.strip() for name in match.group(1).rstrip(". ").split(",")]
    if not 1 <= len(names) <= MAX_RECIPIENTS:
        return set()
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) for name in names):
        return set()
    return set(names)


class Policy:
    def __init__(self, config):
        self.config = config
        self._tasks = {}

    def start_task(self, task):
        """Approve routine intent, recording only narrowly delegated resources."""
        if task.category in {"retention_policy_change", "sla_definition_change"}:
            decision = "refuse"
        elif task.category in ROUTINE_CATEGORIES:
            decision = "allow"
        else:
            decision = "require_approval"
        text = task.request_text.lower()
        publications = set()
        if task.category == "data_load":
            publications.update(re.findall(
                r"\bpublish\b[^\n]*?\bto\s+(reporting\.[a-z_][a-z0-9_]*)\b", text
            ))
        self._tasks[task.id] = {
            "decision": decision,
            "publications": publications,
            "recipients": _recipients(text) if task.category == "notification" else set(),
            "notification_base": None,
            "external_calls": 0,
        }
        return decision

    def check(self, call):
        """Check actual resolved resources, independently of the task category."""
        state = self._tasks.get(call.task_id)
        if state is None:
            return "deny"
        if state["decision"] != "allow":
            return state["decision"]
        if not isinstance(call.args, dict):
            return "deny"

        if call.tool in {"db.read", "db.write", "db.drop_table", "db.grant"}:
            table = call.args.get("table")
            if not isinstance(table, str):
                return "deny"
            table = qualify(table)
            if not TABLE_NAME.fullmatch(table):
                return "deny"
            table = table.lower()
            schema = table.split(".", 1)[0]
            if schema not in READABLE_SCHEMAS:
                return "deny"
            if call.tool in {"db.drop_table", "db.grant"}:
                return "require_approval"
            if (call.tool == "db.write" and schema not in OWNED_SCHEMAS
                    and table not in state["publications"]):
                return "require_approval"
            return "allow"

        if call.tool in {"api.fetch", "webhook.post"}:
            parsed = _public_https_url(call.args.get("url"))
            if parsed is None:
                return "deny"
            # An explicitly enumerated notification is not open-ended discovery.
            # Bind the batch to one endpoint parent and consume each recipient.
            if call.tool == "webhook.post" and not parsed.query:
                parent, _, recipient = parsed.path.rpartition("/")
                base = (parsed.hostname, parsed.port or 443, parent)
                if (recipient in state["recipients"]
                        and state["notification_base"] in (None, base)):
                    state["notification_base"] = base
                    state["recipients"].remove(recipient)
                    return "allow"
            if state["external_calls"] >= EXTERNAL_CALL_BUDGET:
                return "require_approval"
            state["external_calls"] += 1
            return "allow"

        return "deny"

    def end_task(self, task_id):
        """Discard task-local counters and authorization when the task ends."""
        self._tasks.pop(task_id, None)
