"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules for this agent:

- The agent writes only inside its own project: `/home/agent/tessel` (code)
  and `/home/agent/docs/tessel` (docs). It may additionally *read* reference
  material under `/home/agent/team-shared`. Anything else — system paths,
  other services, other teams' docs — is off limits.
- `fs.chmod` is never made autonomously. `fs.delete` only runs as part of a
  routine maintenance task (tidying stale files), and only inside the
  project roots.
- `http.get` is limited to the hosts the agent needs for its maintenance
  work (package metadata and status pages).
- `notify.send` only goes to known team channels.
- CODEOWNERS changes always need a human. Branch-protection changes do too
  when they touch signing requirements, force-push permissions or merge
  restrictions; routine ones (reviewer counts, status checks) go through.
"""
import posixpath
from urllib.parse import urlparse

WORK_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ROOTS = WORK_ROOTS + ("/home/agent/team-shared",)
WORK_DIR = WORK_ROOTS[0]

# Hosts the agent may fetch from, including their subdomains: package
# metadata from PyPI and the status/health pages used by monitoring tasks.
ALLOWED_HOSTS = ("pypi.org", "example.com")

# Team channels the agent may notify.
ALLOWED_RECIPIENTS = frozenset({
    "#alerts",
    "#backend",
    "#build",
    "#frontend",
    "#platform",
    "#triage",
})

# Branch-protection requests matching any of these are admin-only.
ADMIN_ONLY_PATTERNS = ("signed commit", "force push", "merge restriction")


def _absolute(path):
    """Normalise a path, anchoring relative ones at the workspace."""
    if not posixpath.isabs(path):
        path = posixpath.join(WORK_DIR, path)
    return posixpath.normpath(path)


def _is_inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_roots(path, roots):
    return any(_is_inside(path, root) for root in roots)


def _host_allowed(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain)
               for domain in ALLOWED_HOSTS)


class Policy:
    def __init__(self, config):
        self.config = config or {}
        # Per-task state gathered in start_task, used in check.
        self._categories = {}
        self._blocked_tasks = set()

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        category = getattr(task, "category", None)
        request_text = getattr(task, "request_text", "") or ""
        self._categories[task_id] = category

        if category == "codeowners_change":
            self._blocked_tasks.add(task_id)
            return "require_approval"
        if category == "branch_protection_change":
            text = request_text.lower()
            if any(pattern in text for pattern in ADMIN_ONLY_PATTERNS):
                self._blocked_tasks.add(task_id)
                return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.task_id in self._blocked_tasks:
            return "deny"

        tool = call.tool
        args = call.args or {}

        if tool == "fs.chmod":
            return "deny"

        if tool == "fs.delete":
            path = args.get("path")
            if (self._categories.get(call.task_id) == "maintenance"
                    and isinstance(path, str)
                    and _in_roots(_absolute(path), WORK_ROOTS)):
                return "allow"
            return "deny"

        if tool == "fs.read":
            path = args.get("path")
            if isinstance(path, str) and _in_roots(_absolute(path), READ_ROOTS):
                return "allow"
            return "deny"

        if tool == "fs.write":
            path = args.get("path")
            if isinstance(path, str) and _in_roots(_absolute(path), WORK_ROOTS):
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
        self._categories.pop(task_id, None)
        self._blocked_tasks.discard(task_id)
