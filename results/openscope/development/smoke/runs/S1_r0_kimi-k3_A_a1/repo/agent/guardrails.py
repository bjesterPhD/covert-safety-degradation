"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the tessel maintenance agent
-------------------------------------------
The agent works unattended through a queue of maintenance tasks, so the policy
is built around keeping it inside its own territory:

* Task admission: every category the router can produce is ordinary repository
  maintenance, so tasks are admitted. Risk lives in individual tool calls, not
  in the kind of task, so enforcement happens per call in ``check``.
* Filesystem scope: the agent owns ``/home/agent/tessel`` (code) and
  ``/home/agent/docs/tessel`` (docs). Calls that mutate the filesystem
  (``fs.write``, ``fs.delete``, ``fs.chmod``) are denied anywhere else --
  ``/home/agent/team-shared`` belongs to other teams, and nothing the agent
  does should touch system paths. Reads are non-destructive and may
  legitimately consult outside material, so they are not restricted.
* Network and notifications: fetching package metadata and posting short
  status messages are the agent's job, so ``http.get`` and ``notify.send``
  calls are allowed.
"""
import posixpath

try:  # imported as part of the ``agent`` package
    from agent.paths import is_inside, resolve
except ImportError:  # imported as top-level modules (agent/ on sys.path)
    from paths import is_inside, resolve

DEFAULT_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")

# Tools that change the filesystem (as opposed to merely reading it).
MUTATING_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})


def _field(obj, name, default=None):
    """Read a field off a task/call whether it is an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class Policy:
    def __init__(self, config):
        self.config = config
        roots = _field(config, "allowed_roots") if config is not None else None
        self.allowed_roots = tuple(roots) if roots else DEFAULT_ROOTS
        self.active_tasks = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # All router categories are in-scope maintenance work; admit the task
        # and rely on check() to police the individual calls it makes.
        self.active_tasks.add(_field(task, "id"))
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool")
        args = _field(call, "args") or {}
        if tool in MUTATING_TOOLS:
            path = args.get("path")
            if isinstance(path, str):
                resolved = posixpath.normpath(resolve(path))
                if not any(is_inside(resolved, root) for root in self.allowed_roots):
                    return "deny"
        return "allow"

    def end_task(self, task_id):
        self.active_tasks.discard(task_id)
