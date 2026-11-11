"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change repository governance (licence, CI, tags, submodules,
    renames, major upgrades, CODEOWNERS, branch protection) are not routine
    maintenance; they need a human to approve them, and the agent does no work
    for them until then.
  * File tools may only touch the agent's own project and docs. Anything else
    (including other teams' material) is denied. Paths are normalised first so
    `..` and relative paths cannot escape.
  * Deleting files and changing permission bits are irreversible or security
    relevant and always need approval.
  * Fan-out is limited: one task may contact only a few distinct hosts and
    notify only a few distinct recipients.
"""
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

ALLOWED_ROOTS = (WORK_DIR, "/home/agent/docs/tessel")

GOVERNANCE_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
})

APPROVAL_TOOLS = frozenset({"fs.delete", "fs.chmod"})

MAX_HOSTS_PER_TASK = 3
MAX_RECIPIENTS_PER_TASK = 3


class Policy:
    def __init__(self, config=None):
        self.config = config
        self._gated = {}       # task_id -> decision given at start
        self._hosts = {}       # task_id -> set of hosts contacted
        self._recipients = {}  # task_id -> set of recipients notified

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._hosts[task.id] = set()
        self._recipients[task.id] = set()
        if task.category in GOVERNANCE_CATEGORIES:
            self._gated[task.id] = "require_approval"
            return "require_approval"
        self._gated.pop(task.id, None)
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        gated = self._gated.get(call.task_id)
        if gated:
            return gated

        tool = call.tool
        args = call.args or {}

        if tool in PATH_TOOLS:
            path = args.get("path")
            if not isinstance(path, str) or not path:
                return "deny"
            resolved = resolve(path)
            if not any(is_inside(resolved, root) for root in ALLOWED_ROOTS):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            host = urlsplit(url).hostname if isinstance(url, str) else None
            if not host:
                return "deny"
            hosts = self._hosts.setdefault(call.task_id, set())
            if host not in hosts and len(hosts) >= MAX_HOSTS_PER_TASK:
                return "deny"
            hosts.add(host)
            return "allow"

        if tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not recipient:
                return "deny"
            recipients = self._recipients.setdefault(call.task_id, set())
            if recipient not in recipients and len(recipients) >= MAX_RECIPIENTS_PER_TASK:
                return "deny"
            recipients.add(recipient)
            return "allow"

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        self._gated.pop(task_id, None)
        self._hosts.pop(task_id, None)
        self._recipients.pop(task_id, None)
