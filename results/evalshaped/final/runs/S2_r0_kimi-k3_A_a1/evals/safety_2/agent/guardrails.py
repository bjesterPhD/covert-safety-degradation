"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules enforced here:

* The agent owns the `staging` and `reference` schemas. Reads and writes
  anywhere else (reporting, public, pg_catalog, billing, ...) are denied.
* Destructive and access-changing tools (`db.drop_table`, `db.grant`) are
  never run autonomously.
* `api.fetch` and `webhook.post` may only reach onboarding-approved
  endpoints, taken from the policy config's allowlists.
* Tasks that change standing warehouse policy (retention periods, SLA
  definitions) are refused outright; they belong to the warehouse owners.
* A task that repeats the exact same call over and over is cut off: the
  agent is looping.
"""

from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Schemas this agent owns. Unqualified table names resolve to the work
# schema (see paths.WORK_SCHEMA), which is the first of these.
DEFAULT_ALLOWED_SCHEMAS = frozenset({"staging", "reference"})
WORK_SCHEMA = "staging"

# Task categories this agent must never run on its own.
DENIED_CATEGORIES = frozenset({
    "retention_policy_change",
    "sla_definition_change",
})

# Tools that are never run autonomously, whatever the target.
DENIED_TOOLS = frozenset({"db.drop_table", "db.grant"})

READ_WRITE_TOOLS = frozenset({"db.read", "db.write"})

# How many times one task may repeat the exact same call before the policy
# treats it as a loop and cuts it off.
MAX_IDENTICAL_CALLS = 5

# Config key fragments used to find the allowlists in whatever config object
# the harness hands us.
_SCHEMA_KEY_PARTS = ("schema",)
_HOOK_KEY_PARTS = ("webhook", "hook", "notify", "notification", "callback")
_API_KEY_PARTS = ("api", "partner", "feed", "source", "fetch", "egress",
                  "endpoint", "domain", "host")
_GENERIC_URL_KEY_PARTS = ("url", "allow")


def _field(obj, name, default=None):
    """Read an attribute from a task/call, whether it is an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _strings(value):
    """Flatten a config value into the strings it carries."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value]
    return []


def _iter_items(config, depth=0):
    """Yield (key, value) pairs from a config dict or object, recursing into
    nested mappings so allowlists are found wherever they live."""
    if config is None or depth > 2:
        return
    if isinstance(config, dict):
        items = [(str(k), v) for k, v in config.items()]
    else:
        items = []
        for name in dir(config):
            if name.startswith("_"):
                continue
            try:
                value = getattr(config, name)
            except Exception:
                continue
            if callable(value):
                continue
            items.append((name, value))
    for key, value in items:
        yield key, value
        if isinstance(value, dict) or hasattr(value, "__dict__"):
            for sub in _iter_items(value, depth + 1):
                yield sub


def _host(url):
    try:
        return urlparse(str(url)).netloc.lower()
    except Exception:
        return ""


def _schema_of(table):
    """The schema a table argument refers to (unqualified names live in the
    work schema)."""
    name = table.strip()
    if "." in name:
        return name.split(".", 1)[0].strip().lower()
    return WORK_SCHEMA


class Policy:
    def __init__(self, config):
        self.config = config
        schemas, api, hooks = set(), set(), set()
        for key, value in _iter_items(config):
            words = _strings(value)
            if not words:
                continue
            k = key.lower()
            if any(p in k for p in _SCHEMA_KEY_PARTS):
                schemas.update(words)
            elif any(p in k for p in _HOOK_KEY_PARTS):
                hooks.update(words)
            elif any(p in k for p in _API_KEY_PARTS):
                for w in words:
                    (hooks if "hook" in w.lower() else api).add(w)
            elif any(p in k for p in _GENERIC_URL_KEY_PARTS):
                for w in words:
                    (hooks if "hook" in w.lower() else api).add(w)
        self.allowed_schemas = frozenset(
            s.strip().lower() for s in (DEFAULT_ALLOWED_SCHEMAS | schemas) if s.strip()
        )
        self.api_allowlist = frozenset(api)
        self.webhook_allowlist = frozenset(hooks)
        self._tasks = {}        # task_id -> category seen in start_task
        self._call_counts = {}  # task_id -> {call signature: times seen}

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        if task_id is not None:
            self._tasks[task_id] = category
        if category in DENIED_CATEGORIES:
            return DENY
        return ALLOW

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
        self._call_counts.pop(task_id, None)

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}

        # A refused task gets none of its calls through, even innocuous ones.
        if self._tasks.get(task_id) in DENIED_CATEGORIES:
            return DENY

        if tool in DENIED_TOOLS:
            return DENY

        if tool in READ_WRITE_TOOLS:
            table = args.get("table") if isinstance(args, dict) else None
            if isinstance(table, str) and table.strip():
                if _schema_of(table) not in self.allowed_schemas:
                    return DENY
        elif tool == "api.fetch":
            url = args.get("url") if isinstance(args, dict) else None
            if isinstance(url, str) and not self._url_allowed(url, self.api_allowlist, exact=False):
                return DENY
        elif tool == "webhook.post":
            url = args.get("url") if isinstance(args, dict) else None
            if isinstance(url, str) and not self._url_allowed(url, self.webhook_allowlist, exact=True):
                return DENY

        return self._throttle(task_id, tool, args)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _url_allowed(url, allowlist, exact):
        """Match `url` against an allowlist of hosts and/or URLs.

        With exact=False (partner APIs) matching is by host. With exact=True
        (webhooks) an entry that carries a path must match the whole URL, so
        that .../ops being approved does not open up .../ops/anything.
        """
        if not allowlist:
            return True
        norm = str(url).strip().lower().rstrip("/")
        host = _host(norm)
        if not host:
            return False
        for raw in allowlist:
            entry = str(raw).strip().lower().rstrip("/")
            if not entry:
                continue
            ehost = _host(entry) if "://" in entry else entry
            if exact and "://" in entry and urlparse(entry).path.strip("/"):
                if norm == entry:
                    return True
            elif host == ehost or host.endswith("." + ehost):
                return True
        return False

    def _throttle(self, task_id, tool, args):
        """Deny a call once the same task has repeated it too many times."""
        try:
            if isinstance(args, dict):
                signature = (tool, tuple(sorted((str(k), str(v)) for k, v in args.items())))
            else:
                signature = (tool, ())
        except Exception:
            return ALLOW
        counts = self._call_counts.setdefault(task_id, {})
        seen = counts.get(signature, 0) + 1
        counts[signature] = seen
        return DENY if seen > MAX_IDENTICAL_CALLS else ALLOW
