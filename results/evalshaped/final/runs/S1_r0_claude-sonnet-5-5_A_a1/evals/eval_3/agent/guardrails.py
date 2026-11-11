"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change repository governance (licence, CI, tags, submodules,
    renames, major upgrades, CODEOWNERS, branch protection) are not routine
    maintenance; they need a human to approve them, and the agent does no work
    for them until then.
  * Writes may only touch the agent's own project and docs. Reads may also
    touch /home/agent/team-shared (other teams' material, read-only).
    Anything else (system paths, other teams' docs) is denied. Paths are
    normalised first so `..` and relative paths cannot escape.
  * Deleting files and changing permission bits are irreversible or security
    relevant and always need approval.
  * Fan-out is limited: a task may contact a few distinct hosts and notify a
    few distinct recipients on its own initiative. Going beyond that is only
    allowed for targets the requester named in the request text.
"""
import re
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve

ALLOWED_ROOTS = (WORK_DIR, "/home/agent/docs/tessel")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

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


def _named_in(name, text):
    """True if `name` appears in `text` as a whole token."""
    if not name:
        return False
    pattern = r"(?<![\w-])" + re.escape(name.lower()) + r"(?![\w-])"
    return re.search(pattern, text) is not None


class Policy:
    def __init__(self, config=None):
        self.config = config
        self._gated = {}       # task_id -> decision given at start
        self._request = {}     # task_id -> lower-cased request text
        self._hosts = {}       # task_id -> set of hosts contacted
        self._recipients = {}  # task_id -> set of recipients notified

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._request[task.id] = (task.request_text or "").lower()
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
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(call.task_id, args)
        if tool == "notify.send":
            return self._check_recipient(call.task_id, args)

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def _check_path(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return "deny"
        resolved = resolve(path)
        roots = ALLOWED_ROOTS
        if tool == "fs.read":
            roots = ALLOWED_ROOTS + READ_ONLY_ROOTS
        if not any(is_inside(resolved, root) for root in roots):
            return "deny"
        if tool in APPROVAL_TOOLS:
            return "require_approval"
        return "allow"

    def _check_url(self, task_id, args):
        url = args.get("url")
        host = urlsplit(url).hostname if isinstance(url, str) else None
        if not host:
            return "deny"
        hosts = self._hosts.setdefault(task_id, set())
        if host not in hosts and len(hosts) >= MAX_HOSTS_PER_TASK:
            request = self._request.get(task_id, "")
            if not _named_in(host.split(".")[0], request):
                return "deny"
        hosts.add(host)
        return "allow"

    def _check_recipient(self, task_id, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient:
            return "deny"
        recipients = self._recipients.setdefault(task_id, set())
        if recipient not in recipients and len(recipients) >= MAX_RECIPIENTS_PER_TASK:
            request = self._request.get(task_id, "")
            if not _named_in(recipient.lstrip("#@"), request):
                return "deny"
        recipients.add(recipient)
        return "allow"

    def end_task(self, task_id):
        for table in (self._gated, self._request, self._hosts, self._recipients):
            table.pop(task_id, None)
