"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change governance (licence, CI, tags, submodules, repo name,
    major dependency upgrades, branch protection, CODEOWNERS) need a human.
  * File tools may only touch the agent's own project and docs; anything else
    (system files, other services, other teams' material) is denied.
  * Destructive file operations (delete, chmod) always need approval.
  * Fan-out is limited: one task may make only a handful of http.get or
    notify.send calls, so a request cannot spray many hosts or channels.
"""
from urllib.parse import urlparse

from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/srv/sablefish", "/home/agent/docs/sablefish")
FILE_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
APPROVAL_TOOLS = ("fs.delete", "fs.chmod")

NEEDS_APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    "codeowners_change",
})

# Per task, per tool: most calls and most distinct targets allowed.
MAX_CALLS = {"http.get": 5, "notify.send": 5}
MAX_TARGETS = {"http.get": 3, "notify.send": 3}
TARGET_ARG = {"http.get": "url", "notify.send": "recipient"}


def _target(tool, args):
    value = args.get(TARGET_ARG[tool])
    if tool == "http.get" and isinstance(value, str):
        return urlparse(value).hostname or value
    return value


class Policy:
    def __init__(self, config):
        self.config = config
        self._held = {}    # task_id -> decision for tasks stopped at start
        self._calls = {}   # task_id -> {tool: [targets]}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in NEEDS_APPROVAL_CATEGORIES:
            self._held[task.id] = "require_approval"
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        held = self._held.get(call.task_id)
        if held:
            return held

        tool, args = call.tool, call.args or {}

        if tool in FILE_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not any(
                is_inside(resolve(path), root) for root in ALLOWED_ROOTS
            ):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool in MAX_CALLS:
            seen = self._calls.setdefault(call.task_id, {}).setdefault(tool, [])
            target = _target(tool, args)
            distinct = set(seen)
            distinct.add(target)
            if len(seen) + 1 > MAX_CALLS[tool] or len(distinct) > MAX_TARGETS[tool]:
                return "deny"
            seen.append(target)
            return "allow"

        # Unknown tools are not permitted.
        return "deny"

    def end_task(self, task_id):
        self._held.pop(task_id, None)
        self._calls.pop(task_id, None)
