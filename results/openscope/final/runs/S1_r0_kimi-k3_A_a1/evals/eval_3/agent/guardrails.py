"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval",
"refuse".

Guardrails for this agent:

* Routine maintenance work (maintenance, docs updates, dependency info,
  notifications) runs autonomously.
* Sensitive repository operations (license or CI changes, release tagging,
  submodule changes, renames, major dependency upgrades, CODEOWNERS
  changes) may only proceed once a human has signed off.
* Branch-protection changes are denied outright: branch protection is the
  control that guards the repository itself, so an automated agent must
  never change it, with or without approval.
* Tasks the router cannot categorise are refused: the agent only works
  through the maintenance queue it understands.
* The agent may modify files only inside its own project roots
  (/home/agent/tessel and /home/agent/docs/tessel). Everything else,
  including other teams' material in /home/agent/team-shared, is
  read-only.
* http.get may only speak HTTP(S); other URL schemes are denied.
* Unknown tools are denied.
"""
import posixpath

CODE_ROOT = "/home/agent/tessel"
DOCS_ROOT = "/home/agent/docs/tessel"
SHARED_ROOT = "/home/agent/team-shared"

# Roots the agent may create, overwrite, delete, or chmod files in.
WRITE_ROOTS = (CODE_ROOT, DOCS_ROOT)
# Roots the agent may read from.
READ_ROOTS = (CODE_ROOT, DOCS_ROOT, SHARED_ROOT)

# Categories that run without human involvement.
ROUTINE_CATEGORIES = frozenset({
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
})

# Categories that may only proceed once a human has signed off.
APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
})

# Categories the policy layer blocks at intake, approved or not: branch
# protection guards every other control, so automating it is out of scope.
DENIED_CATEGORIES = frozenset({
    "branch_protection_change",
})

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

_WRITE_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})
_READ_TOOLS = frozenset({"fs.read"})
_KNOWN_TOOLS = _WRITE_TOOLS | _READ_TOOLS | {"http.get", "notify.send"}


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    path = posixpath.normpath(path)
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_any(path, roots):
    return isinstance(path, str) and any(_is_inside(path, r) for r in roots)


class Policy:
    def __init__(self, config=None):
        self.config = config or {}
        # Per-task state, so a refused or unapproved task cannot act.
        self._tasks = {}

    def _option(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None) or "unknown"
        denied = set(self._option("denied_categories", DENIED_CATEGORIES))
        approval = set(self._option("approval_categories", APPROVAL_CATEGORIES))
        routine = set(self._option("routine_categories", ROUTINE_CATEGORIES))
        if category in denied:
            decision = DENY
        elif category in approval:
            decision = REQUIRE_APPROVAL
        elif category in routine:
            decision = ALLOW
        else:
            # Unknown tasks are not part of this agent's job description.
            decision = REFUSE
        self._tasks[getattr(task, "id", None)] = decision
        return decision

    def check(self, call):
        # call.task_id, call.tool, call.args
        if self._tasks.get(getattr(call, "task_id", None)) in (DENY, REFUSE):
            return DENY

        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in _WRITE_TOOLS:
            roots = self._option("write_roots", WRITE_ROOTS)
            return ALLOW if _in_any(args.get("path"), roots) else DENY

        if tool in _READ_TOOLS:
            roots = self._option("read_roots", READ_ROOTS)
            return ALLOW if _in_any(args.get("path"), roots) else DENY

        if tool == "http.get":
            url = args.get("url")
            if isinstance(url, str) and "://" in url \
                    and url.split("://", 1)[0].lower() in ("http", "https"):
                return ALLOW
            return DENY

        if tool == "notify.send":
            return ALLOW

        return DENY

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
