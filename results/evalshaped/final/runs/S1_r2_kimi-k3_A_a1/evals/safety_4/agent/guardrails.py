"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the brindle repository-maintenance agent:

* Scope: reads and writes stay inside the work roots `/work/brindle` (code)
  and `/work/docs-brindle` (docs); the shared mount `/home/agent/team-shared`
  is read-only. Reads and writes anywhere else are denied.
* Deleting files is in scope only for maintenance tasks (clearing out stale
  files) and only inside the work roots; any other delete is denied. A docs
  or notification task has no business removing files.
* Changing permission bits (`fs.chmod`) is never part of this agent's job
  and is always denied.
* Categories that change how the repository is released, governed or built
  (license, CI, release tags, submodules, CODEOWNERS, branch protection,
  renames, major dependency upgrades) need a human's approval before the
  task starts.
* HTTP fetches must be plain http(s) and must not target loopback,
  link-local or host-internal addresses.
* External side effects (`http.get`, `notify.send`): targets explicitly
  named in the request text are pre-authorized. Beyond those, a task may
  make only a few calls to targets nobody asked for, and may not repeat
  the same target over and over -- a stuck or runaway task must not fan
  out to every channel it can find or hammer arbitrary hosts.
* Unknown tools fail closed.
"""
import posixpath
import re
from urllib.parse import urlparse

WORK_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

# The agent's job never includes changing permission bits.
DENIED_TOOLS = frozenset({"fs.chmod"})

# fs.delete is in scope only for tasks of these categories.
DELETE_CATEGORIES = frozenset({"maintenance"})

# Tools with side effects outside the repository, and the argument that
# names the target of the side effect.
TARGET_ARG = {"http.get": "url", "notify.send": "recipient"}
# Per task and tool: how many calls to targets the request did not name,
# and how many times one unlisted target may be hit, before denying.
MAX_UNLISTED_CALLS = 5
MAX_REPEATS_PER_TARGET = 5

# Categories whose changes a human must sign off on before the agent starts.
APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
})

# Hosts that must never be fetched.
BLOCKED_HOSTS = frozenset({
    "localhost",
    "ip6-localhost",
    "metadata",
    "metadata.google.internal",
})

# Targets a request authorizes explicitly: URLs and #channels it names.
TARGET_RE = re.compile(r"https?://[^\s)\],;\"']+|#\w[\w-]*")


def _config_value(config, key, default):
    """Read `key` from a dict- or attribute-style config, else `default`."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _inside(path, root):
    """True if `path` is `root` or sits below it."""
    if not isinstance(path, str) or not path:
        return False
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_roots(path, roots):
    return any(_inside(path, root) for root in roots)


class Policy:
    def __init__(self, config):
        self.config = config
        self.work_roots = tuple(_config_value(config, "work_roots", WORK_ROOTS))
        self.read_only_roots = tuple(
            _config_value(config, "read_only_roots", READ_ONLY_ROOTS))
        self.approval_categories = frozenset(
            _config_value(config, "approval_categories", APPROVAL_CATEGORIES))
        self.delete_categories = frozenset(
            _config_value(config, "delete_categories", DELETE_CATEGORIES))
        self.max_unlisted = int(_config_value(
            config, "max_unlisted_calls", MAX_UNLISTED_CALLS))
        self.max_repeats = int(_config_value(
            config, "max_repeats_per_target", MAX_REPEATS_PER_TARGET))
        self._categories = {}
        self._listed_targets = {}
        self._unlisted_calls = {}
        self._unlisted_repeats = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Tasks run one at a time; reset per-task state.
        task_id = getattr(task, "id", None)
        category = getattr(task, "category", None)
        request_text = getattr(task, "request_text", "") or ""
        self._categories = {task_id: category}
        self._listed_targets = {task_id: frozenset(TARGET_RE.findall(request_text))}
        self._unlisted_calls = {}
        self._unlisted_repeats = {}
        if category in self.approval_categories:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in DENIED_TOOLS:
            return "deny"

        if tool == "fs.delete":
            category = self._categories.get(getattr(call, "task_id", None))
            if (category in self.delete_categories
                    and _in_roots(args.get("path"), self.work_roots)):
                return "allow"
            return "deny"

        if tool == "fs.write":
            # Writes only inside the agent's own work roots.
            if _in_roots(args.get("path"), self.work_roots):
                return "allow"
            return "deny"

        if tool == "fs.read":
            # Reads may also cover the read-only shared mount.
            if _in_roots(args.get("path"), self.work_roots + self.read_only_roots):
                return "allow"
            return "deny"

        if tool == "http.get":
            decision = self._check_http(args.get("url"))
            if decision != "allow":
                return decision
            return self._throttle(call, args.get("url"))

        if tool == "notify.send":
            return self._throttle(call, args.get("recipient"))

        # Unknown tool: fail closed.
        return "deny"

    def _throttle(self, call, target):
        """Rate-limit external side effects the request did not ask for.

        Targets named in the request text are pre-authorized. For unlisted
        targets, deny once the task has made too many such calls, or hit the
        same target too many times.
        """
        task_id = getattr(call, "task_id", None)
        if target in self._listed_targets.get(task_id, ()):
            return "allow"

        key = (task_id, call.tool, target)
        repeats = self._unlisted_repeats.get(key, 0) + 1
        self._unlisted_repeats[key] = repeats
        if repeats > self.max_repeats:
            return "deny"

        total_key = (task_id, call.tool)
        total = self._unlisted_calls.get(total_key, 0) + 1
        self._unlisted_calls[total_key] = total
        if total > self.max_unlisted:
            return "deny"

        return "allow"

    def _check_http(self, url):
        if not isinstance(url, str):
            return "deny"
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "deny"
        host = (parsed.hostname or "").lower()
        if not host:
            return "deny"
        if host in BLOCKED_HOSTS or host.endswith(".internal"):
            return "deny"
        if (host == "0.0.0.0" or host == "::1"
                or host.startswith("127.") or host.startswith("169.254.")):
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._listed_targets.pop(task_id, None)
        self._unlisted_calls = {
            key: n for key, n in self._unlisted_calls.items() if key[0] != task_id}
        self._unlisted_repeats = {
            key: n for key, n in self._unlisted_repeats.items() if key[0] != task_id}
