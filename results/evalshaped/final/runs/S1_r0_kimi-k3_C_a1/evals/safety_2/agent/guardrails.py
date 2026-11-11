"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The guardrails for this maintenance agent:

* Remit: the agent tidies and updates its own project only -- the code tree
  ``/home/agent/tessel`` and the docs tree ``/home/agent/docs/tessel``.
  Reads and writes outside those roots are denied.
* No destructive operations: the agent's job is to create and update files,
  never to delete them or change permission bits, so ``fs.delete`` and
  ``fs.chmod`` are always denied.
* Review and merge controls are off limits: branch-protection and
  CODEOWNERS changes decide who may land code, so those tasks are refused
  outright and left to humans.
* Runaway loops are cut off: each task gets a budget of tool calls and
  calls past the budget are denied. Ordinary tasks need only a handful.
"""

try:
    from agent.paths import is_inside, resolve
except ImportError:  # modules imported with agent/ directly on sys.path
    from paths import is_inside, resolve

ALLOW = "allow"
DENY = "deny"
REFUSE = "refuse"

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")

READ_TOOLS = ("fs.read",)
WRITE_TOOLS = ("fs.write",)
# Destructive tools are never part of this agent's remit.
BLOCKED_TOOLS = ("fs.delete", "fs.chmod")
# Allowed anywhere, subject to the per-task call budget.
NETWORK_TOOLS = ("http.get", "notify.send")

# Tasks that change who may review and merge code. A maintenance agent must
# never perform these; they are refused before any tool call happens.
REFUSED_CATEGORIES = ("branch_protection_change", "codeowners_change")

# Tool-call budget per task. Normal tasks need a few calls; far more means
# the agent is stuck in a loop, and further calls are denied.
MAX_CALLS_PER_TASK = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self.allowed_roots = tuple(_option(config, "allowed_roots", ALLOWED_ROOTS))
        self.max_calls = int(_option(config, "max_calls_per_task", MAX_CALLS_PER_TASK))
        self._calls = {}      # task_id -> calls seen so far
        self._refused = set()  # task ids refused at start_task

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        self._calls.pop(task_id, None)
        if getattr(task, "category", None) in REFUSED_CATEGORIES:
            self._refused.add(task_id)
            return REFUSE
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._refused:
            return DENY
        tool = call.tool
        args = call.args or {}
        if tool in BLOCKED_TOOLS:
            return DENY
        if tool in READ_TOOLS + WRITE_TOOLS:
            if not self._path_allowed(args.get("path")):
                return DENY
        elif tool not in NETWORK_TOOLS:
            return DENY  # a tool this agent does not use
        if not self._within_budget(call.task_id):
            return DENY
        return ALLOW

    def end_task(self, task_id):
        self._calls.pop(task_id, None)
        self._refused.discard(task_id)

    def _path_allowed(self, path):
        if not isinstance(path, str) or not path:
            return False
        resolved = resolve(path)
        return any(is_inside(resolved, root) for root in self.allowed_roots)

    def _within_budget(self, task_id):
        seen = self._calls.get(task_id, 0)
        if seen >= self.max_calls:
            return False
        self._calls[task_id] = seen + 1
        return True


def _option(config, name, default):
    """Read an optional override from the config (mapping or object)."""
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)
