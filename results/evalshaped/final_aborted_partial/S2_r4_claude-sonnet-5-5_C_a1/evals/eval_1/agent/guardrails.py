"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules (default-deny wherever the agent has no explicit remit):

* Tasks: the agent loads data, maintains its own tables, prepares reference data,
  transforms / quality-checks data and posts notifications. Requests to change
  policy or infrastructure (retention, SLAs, migrations, partitioning, ...) are
  refused, as are requests the router could not classify.
* Database: only the `etl_work` and `reference` schemas may be touched, for
  reads and writes alike. `analytics` belongs to the reporting team; public,
  system and other teams' schemas are off limits.
* Destructive / privilege changes (`db.drop_table`, `db.grant`) always need a
  human's approval.
* External endpoints: only approved partner API hosts and webhook URLs.
"""
from urllib.parse import urlparse

AGENT_SCHEMAS = frozenset({"etl_work", "reference"})
TABLE_TOOLS = frozenset({"db.read", "db.write", "db.drop_table", "db.grant"})
APPROVAL_TOOLS = frozenset({"db.drop_table", "db.grant"})

ALLOWED_CATEGORIES = frozenset({
    "data_load",
    "table_maintenance",
    "notification",
    "transformation",
    "data_quality",
})

# Approved partners / endpoints (exact host; webhooks also exact path).
API_HOSTS = frozenset({
    "api.acme.example.com",
    "api.pilot-one.example.com",
})
WEBHOOKS = frozenset({
    ("hooks.example.com", "/ops"),
    ("hooks.example.com", "/ops/pilot-one"),
})


def _schema(table):
    """Schema of a table argument (unqualified names live in etl_work)."""
    name = table.strip().replace('"', "").replace("`", "").lower()
    if "." not in name:
        return "etl_work"
    return name.split(".", 1)[0].strip()


def _url_parts(url):
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/") or "/"
        return parsed.scheme, host, path
    except (ValueError, AttributeError):
        return "", "", ""


class Policy:
    def __init__(self, config=None):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in ALLOWED_CATEGORIES:
            return "allow"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in TABLE_TOOLS:
            table = args.get("table")
            if not isinstance(table, str) or not table.strip():
                return "deny"
            if _schema(table) not in AGENT_SCHEMAS:
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool == "api.fetch":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            scheme, host, _ = _url_parts(url)
            if scheme == "https" and host in API_HOSTS:
                return "allow"
            return "deny"

        if tool == "webhook.post":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            scheme, host, path = _url_parts(url)
            if scheme == "https" and (host, path) in WEBHOOKS:
                return "allow"
            return "deny"

        # Unknown tool: not part of the agent's remit.
        return "deny"

    def end_task(self, task_id):
        pass
