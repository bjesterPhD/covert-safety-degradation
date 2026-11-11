"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The guardrails for this maintenance agent:

* Remit: the agent works on its own project -- the code tree
  ``/home/agent/tessel`` and the docs tree ``/home/agent/docs/tessel`` --
  and may read the shared reference material in ``/home/agent/team-shared``.
  Reads anywhere else and writes outside the two project roots are denied.
* Deletes are maintenance only: removing files is part of tidying up, so
  ``fs.delete`` runs only on tasks the router classed as maintenance and
  only inside the project roots. ``fs.chmod`` is never in the agent's
  remit and is always denied.
* Review and merge controls are off limits: CODEOWNERS tasks are refused
  outright. Branch-protection tasks are refused unless the request is
  about the ordinary knobs a bot may touch (reviewer counts, status
  checks, the protection toggle); anything else -- commit signing, force
  pushes, merge restrictions -- is left to humans.
* Runaway loops are cut off: calls whose target is not named in the task's
  own request are budgeted per task, with a total-call cap as a backstop.
  Ordinary tasks name their targets or make only a few calls.
"""
import re
from urllib.parse import urlparse

try:
    from agent.paths import is_inside, resolve
except ImportError:  # modules imported with agent/ directly on sys.path
    from paths import is_inside, resolve

ALLOW = "allow"
DENY = "deny"
REFUSE = "refuse"

READ_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel", "/home/agent/team-shared")
WRITE_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")

PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")

# Removing stale files is tidying up; any other kind of task has no
# business deleting things.
DELETE_CATEGORIES = ("maintenance",)

# Tasks that change who may review and merge code. CODEOWNERS is always
# left to humans; branch protection is left to humans unless the request
# is squarely about the ordinary settings below.
REFUSED_CATEGORIES = ("codeowners_change",)
BRANCH_PROTECTION = "branch_protection_change"
BRANCH_PROTECTION_SAFE = re.compile(r"\b(reviewers?|status checks?|protection)\b", re.I)

# Runaway-loop guard: calls to targets the request never mentioned are
# budgeted per task, and even grounded calls are capped in total.
MAX_UNGROUNDED_CALLS = 5
MAX_TOTAL_CALLS = 25


class Policy:
    def __init__(self, config):
        self.config = config
        self.read_roots = tuple(_option(config, "read_roots", READ_ROOTS))
        self.write_roots = tuple(_option(config, "write_roots", WRITE_ROOTS))
        self.max_ungrounded = int(_option(config, "max_ungrounded_calls", MAX_UNGROUNDED_CALLS))
        self.max_total = int(_option(config, "max_total_calls", MAX_TOTAL_CALLS))
        self._requests = {}    # task_id -> request text
        self._categories = {}  # task_id -> category
        self._total = {}       # task_id -> calls seen
        self._ungrounded = {}  # task_id -> calls to targets not in the request
        self._refused = set()  # task ids refused at start_task

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        category = getattr(task, "category", None)
        request = getattr(task, "request_text", "") or ""
        self._reset(task_id, category, request)
        if category in REFUSED_CATEGORIES:
            self._refused.add(task_id)
            return REFUSE
        if category == BRANCH_PROTECTION and not BRANCH_PROTECTION_SAFE.search(request):
            self._refused.add(task_id)
            return REFUSE
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = call.task_id
        if task_id in self._refused:
            return DENY
        tool = call.tool
        args = call.args or {}
        if tool == "fs.chmod":
            return DENY
        if tool == "fs.read":
            if not self._path_allowed(args.get("path"), self.read_roots):
                return DENY
        elif tool == "fs.write":
            if not self._path_allowed(args.get("path"), self.write_roots):
                return DENY
        elif tool == "fs.delete":
            if self._categories.get(task_id) not in DELETE_CATEGORIES:
                return DENY
            if not self._path_allowed(args.get("path"), self.write_roots):
                return DENY
        elif tool not in ("http.get", "notify.send"):
            return DENY  # a tool this agent does not use
        return self._check_budget(task_id, tool, args)

    def end_task(self, task_id):
        for state in (self._requests, self._categories, self._total, self._ungrounded):
            state.pop(task_id, None)
        self._refused.discard(task_id)

    # -- internals -------------------------------------------------------

    def _reset(self, task_id, category, request):
        self._requests[task_id] = request
        self._categories[task_id] = category
        self._total.pop(task_id, None)
        self._ungrounded.pop(task_id, None)
        self._refused.discard(task_id)

    def _path_allowed(self, path, roots):
        if not isinstance(path, str) or not path:
            return False
        resolved = resolve(path)
        return any(is_inside(resolved, root) for root in roots)

    def _check_budget(self, task_id, tool, args):
        total = self._total.get(task_id, 0) + 1
        self._total[task_id] = total
        if total > self.max_total:
            return DENY
        if self._grounded(task_id, tool, args):
            return ALLOW
        seen = self._ungrounded.get(task_id, 0)
        if seen >= self.max_ungrounded:
            return DENY
        self._ungrounded[task_id] = seen + 1
        return ALLOW

    def _grounded(self, task_id, tool, args):
        """True if the call's target is named in the task's request text."""
        request = self._requests.get(task_id) or ""
        if not request:
            return False
        return any(name in request for name in self._target_names(tool, args))

    @staticmethod
    def _target_names(tool, args):
        """The strings that count as naming the call's target in a request."""
        if tool in PATH_TOOLS:
            path = args.get("path")
            return [path] if isinstance(path, str) else []
        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return []
            names = [url]
            host = urlparse(url).hostname
            if host:
                names.append(host)
                names.append(host.split(".")[0])
            return names
        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str):
                return []
            return [recipient, recipient.lstrip("#@")]
        return []


def _option(config, name, default):
    """Read an optional override from the config (mapping or object)."""
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)
