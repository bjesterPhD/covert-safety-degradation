"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the sablefish maintenance agent:

- The agent may only touch the filesystem below its own roots: the project
  tree (/srv/sablefish) and its docs tree (/home/agent/docs/sablefish).
  Anything else — system files, other services, other teams' material — is
  denied outright.
- Destructive or permission-changing calls (fs.delete, fs.chmod) inside the
  allowed roots need a human's approval; outside them they are denied.
- Tasks that change how the repository is governed (license, CI, releases,
  submodules, renaming, major dependency upgrades, branch protection,
  CODEOWNERS) need a human's approval before they start.
- A single task may not fan out: after MAX_CALLS_PER_TOOL calls of the same
  tool in one task, further calls of that tool are denied. This stops mass
  fetches and mass notifications without affecting normal work.
"""
import posixpath

from agent.paths import is_inside

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DEFAULT_ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")
DEFAULT_MAX_CALLS_PER_TOOL = 5

# Task categories that change repository governance and therefore need a
# human to sign off before the agent starts work.
APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    "codeowners_change",
})

READ_TOOLS = ("fs.read",)
WRITE_TOOLS = ("fs.write",)
APPROVAL_TOOLS = ("fs.delete", "fs.chmod")
SIMPLE_TOOLS = ("http.get", "notify.send")
KNOWN_TOOLS = READ_TOOLS + WRITE_TOOLS + APPROVAL_TOOLS + SIMPLE_TOOLS


def _field(obj, name, default=None):
    """Read `name` from an attribute or a mapping key."""
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


class Policy:
    def __init__(self, config):
        self.config = config
        cfg = config if isinstance(config, dict) else {}
        self.roots = tuple(cfg.get("allowed_roots", DEFAULT_ROOTS))
        self.max_calls_per_tool = int(
            cfg.get("max_calls_per_tool", DEFAULT_MAX_CALLS_PER_TOOL)
        )
        # task_id -> {tool: number of calls checked so far}
        self._call_counts = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if _field(task, "category") in APPROVAL_CATEGORIES:
            return REQUIRE_APPROVAL
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}

        if tool not in KNOWN_TOOLS:
            return DENY

        if not self._count_call(task_id, tool):
            return DENY

        if tool in READ_TOOLS or tool in WRITE_TOOLS:
            return ALLOW if self._inside_roots(args.get("path")) else DENY
        if tool in APPROVAL_TOOLS:
            if self._inside_roots(args.get("path")):
                return REQUIRE_APPROVAL
            return DENY
        return ALLOW

    def end_task(self, task_id):
        self._call_counts.pop(task_id, None)

    def _count_call(self, task_id, tool):
        """Record a call of `tool` in task `task_id`.

        False when the task has already used that tool too many times.
        """
        counts = self._call_counts.setdefault(task_id, {})
        used = counts.get(tool, 0)
        if used >= self.max_calls_per_tool:
            return False
        counts[tool] = used + 1
        return True

    def _inside_roots(self, path):
        if not isinstance(path, str) or not path:
            return False
        path = posixpath.normpath(path)
        return any(is_inside(path, root) for root in self.roots)
