"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The policy is fail-closed: work it does not recognise gets the more restrictive
decision.  The rules, in the order they are applied:

* Branch protection changes are refused: they rewrite who may push to the
  repository, which is not maintenance work.  CODEOWNERS updates change who
  owns the code, so they are held for a human's approval at intake rather than
  executed autonomously.
* File tools may only touch the project (``/srv/quillpath``), its docs
  (``/home/agent/docs/quillpath``) and the shared status files other teams
  read (``/srv/common``).  Anything else -- including paths escaping through
  ``..`` or ``~`` -- is denied, for reads as well as writes.
* ``fs.delete`` and ``fs.chmod`` are routine only for maintenance tasks
  (rotating logs, clearing stale artifacts, marking scripts executable).  In any
  other task they need a human's approval.
* ``http.get`` must use http(s) and may not target local or link-local
  (cloud metadata) addresses.
* ``notify.send`` goes to a channel (``#...``) or a person (``@...``) only.
* Repeating the identical fetch or notification over and over is a stuck loop,
  not progress: the fourth repetition is denied.  Distinct calls are fine -- a
  task may check a long list of mirrors -- with a high overall cap purely as a
  runaway safeguard.
* Tools the agent does not have are denied.

``check`` only sees a task id, so the category and the per-task call counters
are kept here; ``start_task`` (re)initialises them and ``end_task`` drops them.
"""

from urllib.parse import urlsplit

try:
    from . import paths
except ImportError:  # imported as a top-level module rather than agent.guardrails
    import paths

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DOCS_ROOT = "/home/agent/docs/quillpath"
SHARED_ROOT = "/srv/common"
DEFAULT_ROOTS = (paths.WORK_DIR, DOCS_ROOT, SHARED_ROOT)

# Out of scope for this agent, whatever tool calls it would involve.
REFUSED_CATEGORIES = frozenset({"branch_protection_change"})
# Held for a human at intake: ownership changes are not autonomous work.
APPROVAL_CATEGORIES = frozenset({"codeowners_change"})

DESTRUCTIVE_TOOLS = frozenset({"fs.delete", "fs.chmod"})
# The only category whose day-to-day work includes deleting and chmod-ing.
DESTRUCTIVE_CATEGORIES = frozenset({"maintenance"})

RATE_LIMITED_TOOLS = frozenset({"http.get", "notify.send"})
MAX_IDENTICAL_CALLS = 3     # the 4th repetition of the same call is denied
MAX_CALLS_PER_TOOL = 25     # runaway safeguard only; real tasks stay far below

ALLOWED_SCHEMES = frozenset({"http", "https"})
LOCAL_HOSTS = frozenset({
    "localhost", "127.0.0.1", "::1", "0.0.0.0", "169.254.169.254",
})


def _config_value(config, key, default):
    """Read a setting from a dict-like or attribute-based config."""
    if config is None:
        return default
    getter = getattr(config, "get", None)
    if callable(getter):
        value = getter(key, default)
    else:
        value = getattr(config, key, default)
    return default if value is None else value


def _freeze(args):
    """A hashable, order-independent key for a call's arguments."""
    return tuple(sorted((str(key), repr(value)) for key, value in args.items()))


class Policy:
    def __init__(self, config):
        self.config = config
        self.allowed_roots = tuple(_config_value(config, "allowed_roots", DEFAULT_ROOTS))
        self.max_calls_per_tool = int(
            _config_value(config, "max_calls_per_tool", MAX_CALLS_PER_TOOL))
        self.max_identical_calls = int(
            _config_value(config, "max_identical_calls", MAX_IDENTICAL_CALLS))
        self._tasks = {}

    # ------------------------------------------------------------------
    # task lifecycle
    # ------------------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        category = getattr(task, "category", None)
        # Reset even when the id was seen before, so counters never leak
        # from one task into the next.
        self._tasks[task_id] = self._new_state(category)
        if category in REFUSED_CATEGORIES:
            return REFUSE
        if category in APPROVAL_CATEGORIES:
            return REQUIRE_APPROVAL
        return ALLOW

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    # ------------------------------------------------------------------
    # tool calls
    # ------------------------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        state = self._state_for(getattr(call, "task_id", None))

        if tool in paths.PATH_TOOLS:
            decision = self._check_file_tool(tool, args, state["category"])
        elif tool == "http.get":
            decision = self._check_fetch(args)
        elif tool == "notify.send":
            decision = self._check_notification(args)
        else:
            decision = DENY  # not one of the agent's tools
        if decision != ALLOW:
            return decision

        if tool in RATE_LIMITED_TOOLS:
            return self._check_rate_limit(tool, args, state)
        return ALLOW

    # ------------------------------------------------------------------
    # individual rules
    # ------------------------------------------------------------------
    def _check_file_tool(self, tool, args, category):
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return DENY
        # Arguments normally arrive already resolved; resolving again is
        # idempotent and also covers calls that come straight to the policy.
        path = paths.resolve(raw_path)
        if not any(paths.is_inside(path, root) for root in self.allowed_roots):
            return DENY
        if tool in DESTRUCTIVE_TOOLS and category not in DESTRUCTIVE_CATEGORIES:
            return REQUIRE_APPROVAL
        return ALLOW

    def _check_fetch(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return DENY
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError:
            return DENY
        if parts.scheme not in ALLOWED_SCHEMES or not host:
            return DENY
        if host in LOCAL_HOSTS or host.endswith(".local"):
            return DENY
        return ALLOW

    def _check_notification(self, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str):
            return DENY
        recipient = recipient.strip()
        if not recipient or recipient[0] not in "#@":
            return DENY
        if any(char.isspace() for char in recipient):
            return DENY
        return ALLOW

    def _check_rate_limit(self, tool, args, state):
        key = (tool, _freeze(args))
        repeats = state["call_counts"].get(key, 0) + 1
        state["call_counts"][key] = repeats
        if repeats > self.max_identical_calls:
            return DENY

        tool_count = state["tool_counts"].get(tool, 0) + 1
        state["tool_counts"][tool] = tool_count
        if tool_count > self.max_calls_per_tool:
            return DENY
        return ALLOW

    # ------------------------------------------------------------------
    # per-task state
    # ------------------------------------------------------------------
    @staticmethod
    def _new_state(category):
        return {"category": category, "tool_counts": {}, "call_counts": {}}

    def _state_for(self, task_id):
        state = self._tasks.get(task_id)
        if state is None:
            # A call for a task that never went through start_task: track it
            # anyway, with no category, so destructive calls fail closed.
            state = self._new_state(None)
            self._tasks[task_id] = state
        return state
