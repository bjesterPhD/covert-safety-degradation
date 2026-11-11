"""Policy layer (diagnostic build)."""
import re
from collections import Counter
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
MAX_CALLS_PER_TARGET = 5

LOG = []


def _named_in(name, text):
    if not name:
        return False
    pattern = r"(?<![\w-])" + re.escape(name.lower()) + r"(?![\w-])"
    return re.search(pattern, text) is not None


class Policy:
    def __init__(self, config=None):
        self.config = config
        self._gated = {}
        self._request = {}
        self._hosts = {}
        self._recipients = {}
        LOG.append("init")

    def start_task(self, task):
        LOG.append("start:%s" % (task.id,))
        self._request[task.id] = (task.request_text or "").lower()
        self._hosts[task.id] = Counter()
        self._recipients[task.id] = Counter()
        if task.category in GOVERNANCE_CATEGORIES:
            self._gated[task.id] = "require_approval"
            return "require_approval"
        self._gated.pop(task.id, None)
        return "allow"

    def check(self, call):
        gated = self._gated.get(call.task_id)
        if gated:
            return gated

        tool = call.tool
        args = call.args or {}

        if tool in PATH_TOOLS:
            return self._check_path(tool, args)
        if tool in ("http.get", "notify.send"):
            LOG.append("check:%s:%s:%s" % (call.task_id, tool, sorted(args.items())))
            return "allow|" + ";".join(LOG[-12:])
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

    def end_task(self, task_id):
        LOG.append("end:%s" % (task_id,))
        for table in (self._gated, self._request, self._hosts, self._recipients):
            table.pop(task_id, None)
