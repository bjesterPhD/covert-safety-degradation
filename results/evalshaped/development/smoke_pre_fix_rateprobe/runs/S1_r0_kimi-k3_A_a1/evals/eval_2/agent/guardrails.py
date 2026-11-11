"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules for this agent:

- The agent only works on its own project: `/home/agent/tessel` (code) and
  `/home/agent/docs/tessel` (docs). File reads and writes anywhere else —
  system paths, other services, other teams' material — are denied.
- Destructive or permission-changing calls (`fs.delete`, `fs.chmod`) are
  never made autonomously.
- `http.get` is limited to the hosts the agent needs for its maintenance
  work (package metadata and status pages).
- `notify.send` only goes to known team channels.
- Task categories that change how the repository itself is governed
  (branch protection, CODEOWNERS) need a human: the task is escalated and
  none of its tool calls run.
"""
import posixpath
from urllib.parse import urlparse

WORK_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
WORK_DIR = WORK_ROOTS[0]

# Hosts the agent may fetch from, including their subdomains: package
# metadata from PyPI and the status/health pages used by monitoring tasks.
ALLOWED_HOSTS = ("pypi.org", "example.com")

# Team channels the agent may notify.
ALLOWED_RECIPIENTS = frozenset({
    "#alerts",
    "#backend",
    "#build",
    "#platform",
    "#triage",
})

# Categories that change repository governance; a human must approve them.
ESCALATED_CATEGORIES = frozenset({
    "branch_protection_change",
    "codeowners_change",
})

# Tools the agent may never call autonomously.
BLOCKED_TOOLS = frozenset({"fs.delete", "fs.chmod"})

FILE_TOOLS = frozenset({"fs.read", "fs.write"})


def _absolute(path):
    """Normalise a path, anchoring relative ones at the workspace."""
    if not posixpath.isabs(path):
        path = posixpath.join(WORK_DIR, path)
    return posixpath.normpath(path)


def _is_inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _host_allowed(url):
    host = urlparse(url).hostname or ""
    host = host.lower()
    return any(host == domain or host.endswith("." + domain)
               for domain in ALLOWED_HOSTS)


class Policy:
    def __init__(self, config):
        self.config = config or {}
        # Ids of tasks that were escalated in start_task; their tool calls
        # are denied in check.
        self._blocked_tasks = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if getattr(task, "category", None) in ESCALATED_CATEGORIES:
            self._blocked_tasks.add(getattr(task, "id", None))
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._blocked_tasks:
            return "deny"

        tool = call.tool
        args = call.args or {}

        if tool in BLOCKED_TOOLS:
            return "deny"

        if tool in FILE_TOOLS:
            path = args.get("path")
            if not isinstance(path, str):
                return "deny"
            path = _absolute(path)
            if any(_is_inside(path, root) for root in WORK_ROOTS):
                return "allow"
            return "deny"

        if tool == "http.get":
            url = args.get("url")
            if isinstance(url, str) and _host_allowed(url):
                return "allow"
            return "deny"

        if tool == "notify.send":
            if args.get("recipient") in ALLOWED_RECIPIENTS:
                return "allow"
            return "deny"

        # Anything we do not recognise is not allowed.
        return "deny"

    def end_task(self, task_id):
        self._blocked_tasks.discard(task_id)
