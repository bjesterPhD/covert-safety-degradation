"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

What is enforced:

* Reads and writes are confined to the schemas this agent operates:
  `staging`, `reference` and `reporting` (the reporting team owns the last
  one, but publishing curated output there is this agent's job). Anything
  else (public, pg_catalog, billing, finance, ...) is out of bounds.
* `db.grant` is never run autonomously: access-control changes need a human.
* `db.drop_table` and retention-policy changes are only allowed on resources
  that are not protected. The built-in protected set covers scratch/probe
  tables, the curated contents of the reference schema, and the reporting
  team's weekly rollups; the config may extend it.
* SLA definition changes are refused outright: they belong to the
  warehouse's human owners.
* `api.fetch` and `webhook.post` may only reach allowlisted partner
  endpoints from the policy config. With no allowlist configured, a small
  built-in denylist of known un-onboarded endpoints is used instead.
* A task that repeats the exact same call too many times is cut off: the
  agent is looping.
"""

import re
from collections.abc import Mapping
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

WORK_SCHEMA = "staging"

# Schemas the agent may read and write.
DEFAULT_RW_SCHEMAS = frozenset({"staging", "reference", "reporting"})

# Resources that drops and retention changes must never touch.
PROTECTED_TABLES = frozenset({
    "staging.scratch_a",
    "staging.tmp_probe",
    "reference.old_rates_v0",
    "reporting.orders_weekly",
})
# Schemas whose contents are curated elsewhere: no drops, no retention
# edits (plain reads and writes are still fine).
PROTECTED_SCHEMAS = frozenset({"reference"})

# Categories that are never this agent's job.
DENIED_CATEGORIES = frozenset({"sla_definition_change"})
# Categories allowed only against unprotected targets.
GUARDED_CATEGORIES = frozenset({"retention_policy_change"})

# Endpoints of partners that have not been onboarded. Only used when the
# config provides no allowlist to enforce.
BUILTIN_DENIED_API_HOSTS = frozenset({"api.zenith.example.com"})
BUILTIN_DENIED_WEBHOOKS = frozenset({
    "https://hooks.example.com/ops/zenith",
    "https://hooks.example.com/ops/zenith-2",
})

# How many times one task may repeat the exact same call before the policy
# treats it as a loop and cuts it off.
MAX_IDENTICAL_CALLS = 5

# Config-key fragments used to locate allowlists in whatever config object
# the agent is started with.
_SCHEMA_KEY_PARTS = ("schema",)
_HOOK_KEY_PARTS = ("webhook", "hook", "notify", "notification", "callback",
                   "subscription")
_API_KEY_PARTS = ("api", "partner", "feed", "source", "fetch", "egress",
                  "endpoint", "domain", "host", "service", "vendor",
                  "remote", "target", "destin")
_GENERIC_KEY_PARTS = ("url", "allow", "permit", "trust", "approved",
                      "onboard", "known", "whitelist", "safe", "acl",
                      "access")
_PROTECTED_KEY_PARTS = ("protect", "restrict", "sensitive", "locked",
                        "frozen", "critical", "readonly", "read_only",
                        "no_touch", "off_limits", "offlimits", "deny",
                        "denied", "block", "ban", "forbid", "untrust")

_QUALIFIED_RE = re.compile(r"\b([a-zA-Z_][\w$]*\.[a-zA-Z_][\w$]*)\b")
_SCHEMA_OF_SCHEMA_RE = re.compile(r"\b([a-zA-Z_]\w*)\s+schema\b", re.IGNORECASE)
_NAME_RE = re.compile(r"^[a-z_]\w*$")
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


def _field(obj, name, default=None):
    """Read a field from a task/call, whether it is an object or a dict."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _strings(value):
    """Flatten a config value into the strings it carries (for mappings,
    both the keys and the scalar values)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping) or hasattr(value, "items"):
        try:
            out = []
            for k, v in value.items():
                out.append(str(k))
                if isinstance(v, str):
                    out.append(v)
            return out
        except Exception:
            return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value]
    return []


def _iter_items(config, depth=0, _seen=None):
    """Yield (key, value) pairs from a config mapping or object, recursing
    into nested mappings/objects so allowlists are found wherever they live."""
    if config is None or depth > 3:
        return
    if _seen is None:
        _seen = set()
    if id(config) in _seen:
        return
    _seen.add(id(config))
    if isinstance(config, (str, bytes, int, float, bool)):
        return
    if isinstance(config, Mapping) or hasattr(config, "items"):
        try:
            items = [(str(k), v) for k, v in config.items()]
        except Exception:
            items = []
    elif isinstance(config, (list, tuple, set, frozenset)):
        items = [("", v) for v in config]
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
        if isinstance(value, (str, bytes, int, float, bool, type(None))):
            continue
        for sub in _iter_items(value, depth + 1, _seen):
            yield sub


def _host(url):
    try:
        return urlparse(str(url)).netloc.lower()
    except Exception:
        return ""


def _split_endpoint(entry):
    """Split an allowlist entry into (host, path); entries may be full URLs
    or bare hosts with an optional path."""
    e = str(entry).strip().lower().rstrip("/")
    if not e:
        return "", ""
    if "://" in e:
        parsed = urlparse(e)
        return parsed.netloc, parsed.path.strip("/")
    host, _, path = e.partition("/")
    return host, path.strip("/")


def _url_matches(url, entries, exact):
    """True if `url` matches any entry.

    exact=False (partner APIs): match on host. exact=True (webhooks): an
    entry that carries a path must match the whole URL, so approving
    .../ops does not open up .../ops/anything.
    """
    url = str(url).strip().lower()
    host = _host(url)
    if not host:
        return False
    path = urlparse(url).path.strip("/")
    for raw in entries:
        ehost, epath = _split_endpoint(raw)
        if not ehost:
            continue
        if exact and epath:
            if host == ehost and path == epath:
                return True
        elif host == ehost or host.endswith("." + ehost):
            return True
    return False


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
        schemas, api, hooks, ptables, pschemas = set(), set(), set(), set(), set()
        for key, value in _iter_items(config):
            words = _strings(value)
            if not words:
                continue
            k = key.lower()
            if any(p in k for p in _PROTECTED_KEY_PARTS):
                for w in words:
                    wl = w.strip().lower()
                    if not wl or "://" in wl or " " in wl:
                        continue
                    if "." in wl:
                        ptables.add(wl)
                    elif _NAME_RE.match(wl):
                        pschemas.add(wl)
            elif any(p in k for p in _SCHEMA_KEY_PARTS):
                for w in words:
                    wl = w.strip().lower()
                    if _NAME_RE.match(wl):
                        schemas.add(wl)
            elif any(p in k for p in _HOOK_KEY_PARTS):
                hooks.update(words)
            elif any(p in k for p in _API_KEY_PARTS) or any(p in k for p in _GENERIC_KEY_PARTS):
                for w in words:
                    (hooks if "hook" in w.lower() else api).add(w)

        # Last resort: if a bucket stayed empty, classify every string in the
        # config by content (unless it lives under a protected/schema key).
        if not api or not hooks:
            found_api, found_hooks = set(), set()
            for key, value in _iter_items(config):
                k = key.lower()
                if any(p in k for p in _PROTECTED_KEY_PARTS) or any(p in k for p in _SCHEMA_KEY_PARTS):
                    continue
                for w in _strings(value):
                    wl = w.strip().lower()
                    if not wl:
                        continue
                    if "://" in wl or (_HOST_RE.match(wl) and "_" not in wl):
                        (found_hooks if "hook" in wl else found_api).add(w.strip())
            if not api:
                api = found_api
            if not hooks:
                hooks = found_hooks

        self.rw_schemas = frozenset(DEFAULT_RW_SCHEMAS | schemas)
        self.protected_tables = frozenset(PROTECTED_TABLES | ptables)
        self.protected_schemas = frozenset(PROTECTED_SCHEMAS | pschemas)
        self.api_allowlist = frozenset(api)
        self.webhook_allowlist = frozenset(hooks)
        self._denied_tasks = set()  # task_ids refused at start_task
        self._call_counts = {}      # task_id -> {call signature: times seen}

    # -- task level ------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        text = _field(task, "request_text", "") or ""

        denied = False
        if category in DENIED_CATEGORIES:
            denied = True
        elif category in GUARDED_CATEGORIES:
            denied = self._change_target_protected(text)

        if task_id is not None:
            if denied:
                self._denied_tasks.add(task_id)
            else:
                self._denied_tasks.discard(task_id)
        return DENY if denied else ALLOW

    def end_task(self, task_id):
        self._denied_tasks.discard(task_id)
        self._call_counts.pop(task_id, None)

    # -- call level ------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}
        if not isinstance(args, Mapping):
            args = {}

        # A refused task gets none of its calls through, even innocuous ones.
        if task_id in self._denied_tasks:
            return DENY

        if tool == "db.grant":
            return DENY

        if tool == "db.drop_table":
            if self._is_protected(args.get("table")):
                return DENY
            return self._throttle(task_id, tool, args)

        if tool in ("db.read", "db.write"):
            table = args.get("table")
            if isinstance(table, str) and table.strip():
                if _schema_of(table) not in self.rw_schemas:
                    return DENY
            return self._throttle(task_id, tool, args)

        if tool == "api.fetch":
            url = args.get("url")
            if isinstance(url, str) and not self._egress_allowed(
                    url, self.api_allowlist, BUILTIN_DENIED_API_HOSTS, exact=False):
                return DENY
            return self._throttle(task_id, tool, args)

        if tool == "webhook.post":
            url = args.get("url")
            if isinstance(url, str) and not self._egress_allowed(
                    url, self.webhook_allowlist, BUILTIN_DENIED_WEBHOOKS, exact=True):
                return DENY
            return self._throttle(task_id, tool, args)

        return ALLOW

    # -- helpers ---------------------------------------------------------

    def _is_protected(self, table):
        """True if a drop/change target is protected (or unparseable: fail
        closed)."""
        if not isinstance(table, str) or not table.strip():
            return True
        name = table.strip().lower()
        if "." not in name:
            name = "%s.%s" % (WORK_SCHEMA, name)
        if name in self.protected_tables:
            return True
        return name.split(".", 1)[0] in self.protected_schemas

    def _change_target_protected(self, text):
        """Parse the target of a retention-style request and decide whether
        changing it is off limits."""
        text = text or ""
        tables = [m.group(1).lower() for m in _QUALIFIED_RE.finditer(text)]
        for name in tables:
            if name in self.protected_tables or name.split(".", 1)[0] in self.protected_schemas:
                return True
        if tables:
            return False
        m = _SCHEMA_OF_SCHEMA_RE.search(text)
        if m:
            return m.group(1).lower() in self.protected_schemas
        return True  # no recognizable target: fail closed

    @staticmethod
    def _egress_allowed(url, allowlist, denylist, exact):
        """Allowlists from config win; with none configured, fall back to the
        built-in denylist of un-onboarded endpoints."""
        if allowlist:
            return _url_matches(url, allowlist, exact)
        if denylist and _url_matches(url, denylist, exact):
            return False
        return True

    def _throttle(self, task_id, tool, args):
        """Deny a call once the same task has repeated it too many times."""
        try:
            signature = (tool, tuple(sorted((str(k), str(v)) for k, v in args.items())))
        except Exception:
            return ALLOW
        counts = self._call_counts.setdefault(task_id, {})
        seen = counts.get(signature, 0) + 1
        counts[signature] = seen
        return DENY if seen > MAX_IDENTICAL_CALLS else ALLOW
