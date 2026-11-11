"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change governance (licence, CI, tags, submodules, repo name,
    major dependency upgrades, CODEOWNERS, branch protection) need a human to
    approve them; the agent never does them on its own.
  * File tools may only touch the agent's own project and docs; anything else
    (system paths, other teams' material) is denied. The one exception is
    read-only access to the shared team folder, which exists to be read.
  * Destructive or permission-changing file calls (fs.delete, fs.chmod) need
    approval.
  * Fan-out limit: the agent may pick only a few http.get / notify.send targets
    on its own per task, so one vague request cannot hit many hosts or spam many
    channels. Targets the requester named explicitly in the request text do not
    count towards that limit (up to a hard ceiling).
"""
from urllib.parse import urlparse

from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
APPROVAL_TOOLS = ("fs.delete", "fs.chmod")
FAN_OUT_LIMIT = {"http.get": 5, "notify.send": 5}
HARD_LIMIT = 25

APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
}


def _target(tool, args):
    """The host or channel a network call is aimed at, lower-cased."""
    if tool == "http.get":
        url = args.get("url")
        host = urlparse(url).hostname if isinstance(url, str) else None
        return (host or "").lower()
    recipient = args.get("recipient")
    return recipient.lstrip("#@").lower() if isinstance(recipient, str) else ""


def _named_in(request, tool, target):
    """True if the request text itself names this target."""
    if not target:
        return False
    if tool == "http.get":
        # "ca-central" for ca-central.mirror.example.com, or the full host name
        first = target.split(".")[0]
        return target in request or first in request
    return target in request


class Policy:
    def __init__(self, config=None):
        self.config = config
        self._held = {}      # task_id -> decision given at start_task
        self._requests = {}  # task_id -> lower-cased request text
        self._counts = {}    # (task_id, tool) -> calls the agent chose itself
        self._totals = {}    # (task_id, tool) -> all calls

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._requests[task.id] = (task.request_text or "").lower()
        if task.category in APPROVAL_CATEGORIES:
            self._held[task.id] = "require_approval"
            return "require_approval"
        self._held.pop(task.id, None)
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        held = self._held.get(call.task_id)
        if held:
            return held

        tool = call.tool
        args = call.args or {}

        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            path = resolve(path)
            in_project = any(is_inside(path, r) for r in ALLOWED_ROOTS)
            readable = tool == "fs.read" and any(
                is_inside(path, r) for r in READ_ONLY_ROOTS)
            if not (in_project or readable):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"

        limit = FAN_OUT_LIMIT.get(tool)
        if limit is not None:
            key = (call.task_id, tool)
            self._totals[key] = self._totals.get(key, 0) + 1
            if self._totals[key] > HARD_LIMIT:
                return "deny"
            request = self._requests.get(call.task_id, "")
            if not _named_in(request, tool, _target(tool, args)):
                self._counts[key] = self._counts.get(key, 0) + 1
                if self._counts[key] > limit:
                    return "deny"

        return "allow"

    def end_task(self, task_id):
        self._held.pop(task_id, None)
        self._requests.pop(task_id, None)
        for table in (self._counts, self._totals):
            for key in [k for k in table if k[0] == task_id]:
                del table[key]
