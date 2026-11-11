"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Tasks the router could not categorise are refused: an unrecognised
  request must not turn into autonomous warehouse changes.
* Reads (`db.read`) are allowed against any table.
* Calls that change data, structure or access (`db.write`, `db.drop_table`,
  `db.grant`) are denied outside the schemas this agent owns -- by default
  `etl_work` and `ref_data`. The reporting team's `analytics` schema is
  off limits for anything but reads.
* Destructive calls inside owned schemas (`db.drop_table`) require human
  approval; writes and grants on the agent's own tables proceed.
* Outbound calls (`api.fetch`, `webhook.post`) must use https and stay
  within `config["allowed_hosts"]` when that allowlist is configured.
* Unknown tools are denied.

Optional config keys:
    owned_schemas:  schemas the agent may modify (default: etl_work, ref_data)
    allowed_hosts:  host allowlist for api.fetch / webhook.post URLs
"""

from urllib.parse import urlparse

try:  # imported as part of the agent package
    from . import paths
except ImportError:  # imported as a top-level module
    import paths

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

READ_TOOLS = frozenset({"db.read"})
WRITE_TOOLS = frozenset({"db.write", "db.grant"})
APPROVAL_TOOLS = frozenset({"db.drop_table"})
URL_TOOLS = frozenset({"api.fetch", "webhook.post"})

DEFAULT_OWNED_SCHEMAS = frozenset({paths.WORK_SCHEMA, "ref_data"})


def _field(obj, name, default=None):
    """Read `name` from an object or a mapping."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class Policy:
    def __init__(self, config):
        self.config = config or {}

    # -- tasks ---------------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = _field(task, "category")
        if category in (None, "unknown"):
            return REFUSE
        return ALLOW

    def end_task(self, task_id):
        pass

    # -- tool calls ------------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool")
        args = _field(call, "args") or {}
        if tool in paths.TABLE_TOOLS:
            return self._check_table(tool, args)
        if tool in URL_TOOLS:
            return self._check_url(args)
        return DENY

    def _owned_schemas(self):
        schemas = self.config.get("owned_schemas")
        return frozenset(schemas) if schemas else DEFAULT_OWNED_SCHEMAS

    def _check_table(self, tool, args):
        table = args.get("table") if isinstance(args, dict) else None
        if not isinstance(table, str) or not table.strip():
            return DENY
        if tool in READ_TOOLS:
            return ALLOW
        # Args normally arrive resolved, but qualify defensively so an
        # unqualified name cannot slip past the schema check.
        schema = paths.schema_of(paths.qualify(table))
        if schema not in self._owned_schemas():
            return DENY
        if tool in APPROVAL_TOOLS:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_url(self, args):
        url = args.get("url") if isinstance(args, dict) else None
        if not isinstance(url, str):
            return DENY
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return DENY
        allowed_hosts = self.config.get("allowed_hosts")
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            return DENY
        return ALLOW
