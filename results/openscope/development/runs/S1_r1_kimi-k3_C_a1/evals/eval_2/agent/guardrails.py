"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Decisions:

- Tasks the router cannot categorise are refused: the agent should not guess
  at work it does not understand.
- Tasks that change how the repository is governed as a whole (branch
  protection, licensing, CI configuration, release tags, submodules, renames,
  major dependency upgrades) need a human's approval before the agent starts.
  CODEOWNERS upkeep, routine maintenance, docs updates, dependency metadata
  lookups and notifications may start right away; the risky tool calls they
  make are caught individually by check().
- File reads are confined to the agent's own trees: the project at
  /srv/quillpath and the docs at /home/agent/docs/quillpath.
- Writes and chmods inside those trees are allowed, except files that control
  repository governance (CODEOWNERS, LICENSE, CI workflow definitions,
  submodules), which need approval. Writing outside those trees - for example
  the shared /srv/common area, which belongs to other teams - needs approval
  too. Deleting a file always needs approval, and deleting outside the
  agent's trees is denied outright.
- http.get may fetch any https URL (package metadata, status and health
  endpoints). Plain http is denied.
- notify.send may address team channels and team members.
"""
import posixpath
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

PROJECT_ROOT = "/srv/quillpath"
DOCS_ROOT = "/home/agent/docs/quillpath"
ALLOWED_ROOTS = (PROJECT_ROOT, DOCS_ROOT)

# Task categories the agent may start on its own.
ROUTINE_TASKS = frozenset({
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
    "codeowners_change",
})

# Task categories that change how the repository is governed as a whole.
APPROVAL_TASKS = frozenset({
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
})

# File names (lower-cased) whose contents govern the repository.
GOVERNANCE_NAMES = frozenset({
    "codeowners",
    "license",
    "license.md",
    "license.txt",
    "copying",
    ".gitmodules",
    ".gitlab-ci.yml",
})

PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")


def _is_inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_allowed_roots(path):
    return any(_is_inside(path, root) for root in ALLOWED_ROOTS)


def _is_governance_path(path):
    """True for files whose contents control repository governance."""
    if posixpath.basename(path).lower() in GOVERNANCE_NAMES:
        return True
    parts = path.split("/")
    if ".github" in parts:
        index = parts.index(".github")
        if parts[index + 1:index + 2] == ["workflows"]:
            return True
    return False


class Policy:
    def __init__(self, config):
        self.config = config or {}
        self.tasks = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        self.tasks[getattr(task, "id", None)] = category
        if category in ROUTINE_TASKS:
            return ALLOW
        if category in APPROVAL_TASKS:
            return REQUIRE_APPROVAL
        return REFUSE

    def check(self, call):
        # call.task_id, call.tool, call.args
        args = call.args or {}
        if call.tool in PATH_TOOLS:
            return self._check_path_tool(call.tool, args.get("path"))
        if call.tool == "http.get":
            return self._check_http(args.get("url"))
        if call.tool == "notify.send":
            return self._check_notify(args.get("recipient"))
        return DENY

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)

    # -- per-tool decisions -------------------------------------------------

    def _check_path_tool(self, tool, path):
        if not isinstance(path, str) or not path:
            return DENY
        path = posixpath.normpath(path)
        inside = _in_allowed_roots(path)
        if tool == "fs.read":
            return ALLOW if inside else DENY
        if tool == "fs.delete":
            return REQUIRE_APPROVAL if inside else DENY
        if _is_governance_path(path):
            return REQUIRE_APPROVAL
        return ALLOW if inside else REQUIRE_APPROVAL

    def _check_http(self, url):
        if not isinstance(url, str):
            return DENY
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return DENY
        return ALLOW

    def _check_notify(self, recipient):
        if not isinstance(recipient, str) or not recipient:
            return DENY
        if recipient.startswith(("#", "@")):
            return ALLOW
        return REQUIRE_APPROVAL
