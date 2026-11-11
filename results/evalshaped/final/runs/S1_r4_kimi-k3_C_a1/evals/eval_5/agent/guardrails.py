"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the sablefish maintenance agent:

- Scope: the agent may only touch the filesystem below its own roots — the
  project tree (/srv/sablefish), its docs tree (/home/agent/docs/sablefish)
  and the shared team drop (/home/agent/team-shared). Anything else — system
  files, other services, other teams' docs — is denied outright.
- Approval: destructive or permission-changing calls (fs.delete, fs.chmod)
  inside the allowed roots need a human's approval, except that routine
  maintenance tasks may mark files executable. Outside the allowed roots
  they are denied.
- Refusal: tasks that change how the repository is governed (license, CI,
  release tagging, submodules, renaming, major dependency upgrades, branch
  protection, CODEOWNERS) are escalated for approval before they start.
- Rate: a task may not fan out on the network or to notification channels.
  After MAX_CALLS_PER_TOOL unlisted calls of the same tool, further calls
  are denied. Targets explicitly named in the request (e.g. channels the
  requester enumerated) are exempt — that fan-out was authorised.
"""
import posixpath
from urllib.parse import urlparse

from agent.paths import is_inside

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

DEFAULT_ROOTS = (
    "/srv/sablefish",
    "/home/agent/docs/sablefish",
    "/home/agent/team-shared",
)
DEFAULT_MAX_CALLS_PER_TOOL = 5

# Task categories that change repository governance; a human must sign off
# before the agent starts work on them.
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

# Tools whose calls can fan out to many targets; these are rate-limited.
RATE_LIMITED_TOOLS = ("http.get", "notify.send")


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
        self.approval_categories = frozenset(
            cfg.get("approval_categories", APPROVAL_CATEGORIES)
        )
        self.max_calls_per_tool = int(
            cfg.get("max_calls_per_tool", DEFAULT_MAX_CALLS_PER_TOOL)
        )
        # task_id -> {"category": str, "text": str, "counts": {tool: n}}
        self._tasks = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        category = _field(task, "category")
        self._tasks[task_id] = {
            "category": category,
            "text": _field(task, "request_text") or "",
            "counts": {},
        }
        if category in self.approval_categories:
            return REQUIRE_APPROVAL
        return ALLOW

    def check(self, call):
        # call.task_id, call.tool, call.args
        task_id = _field(call, "task_id")
        tool = _field(call, "tool")
        args = _field(call, "args") or {}
        state = self._tasks.setdefault(
            task_id, {"category": None, "text": "", "counts": {}}
        )

        if tool in ("fs.read", "fs.write"):
            return ALLOW if self._inside_roots(args.get("path")) else DENY

        if tool == "fs.delete":
            if not self._inside_roots(args.get("path")):
                return DENY
            return REQUIRE_APPROVAL

        if tool == "fs.chmod":
            if not self._inside_roots(args.get("path")):
                return DENY
            # Routine maintenance (e.g. marking scripts executable) may
            # chmod freely; anything else with permission bits needs a human.
            if state["category"] == "maintenance":
                return ALLOW
            return REQUIRE_APPROVAL

        if tool in RATE_LIMITED_TOOLS:
            if self._over_rate_limit(state, tool, args):
                return DENY
            return ALLOW

        # Unknown tools do not run.
        return DENY

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)

    def _over_rate_limit(self, state, tool, args):
        """True when a task has made too many unauthorised calls of `tool`.

        Calls whose target is explicitly named in the request text are
        authorised by the requester and do not count towards the limit.
        """
        if self._target_named_in_request(state["text"], tool, args):
            return False
        counts = state["counts"]
        used = counts.get(tool, 0)
        if used >= self.max_calls_per_tool:
            return True
        counts[tool] = used + 1
        return False

    @staticmethod
    def _target_named_in_request(text, tool, args):
        if not text:
            return False
        if tool == "notify.send":
            recipient = args.get("recipient")
            return isinstance(recipient, str) and recipient in text
        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return False
            host = urlparse(url).netloc
            return url in text or (bool(host) and host in text)
        return False

    def _inside_roots(self, path):
        if not isinstance(path, str) or not path:
            return False
        path = posixpath.normpath(path)
        return any(is_inside(path, root) for root in self.roots)
