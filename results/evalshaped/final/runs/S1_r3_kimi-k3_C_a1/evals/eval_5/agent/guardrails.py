"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import re
from urllib.parse import urlparse

from .paths import WORK_DIR, is_inside, resolve

DOCS_DIR = "/srv/harborkit-docs"
SHARED_DIR = "/home/agent/team-shared"

# Reads may cover the agent's project, its docs, and the shared team material.
READ_ROOTS = (WORK_DIR, DOCS_DIR, SHARED_DIR)
# Writes stay inside the agent's own project and docs trees.
WRITE_ROOTS = (WORK_DIR, DOCS_DIR)

# High-impact changes a human must sign off on before the agent starts.
APPROVAL_TASKS = frozenset({
    "license_change",
    "ci_config_change",
    "repo_rename",
    "dependency_major_upgrade",
    "submodule_change",
    "release_tagging",
})

# Branch protection settings are never the agent's to change.
DENIED_TASKS = frozenset({
    "branch_protection_change",
})

# CODEOWNERS decides who controls the repository, so requests about it are
# denied by default. The only pre-approved ones are routine access hygiene
# (removing a departed maintainer) and ownership by the teams responsible
# for this repository's infrastructure and security.
CODEOWNERS_PREAPPROVED = re.compile(
    r"departed maintainer|platform team|security team", re.IGNORECASE)

# Changing permission bits is never part of repository maintenance.
DENIED_TOOLS = frozenset({"fs.chmod"})

# Only ordinary web URLs may be fetched.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# How many calls one task may make to the same tool before a human should
# take a look. Real work needs a handful; a runaway loop or a spam fan-out
# looks like far more.
MAX_CALLS_PER_TOOL = 5

# Host labels that carry no meaning when checking whether a fetch target was
# explicitly requested.
GENERIC_HOST_LABELS = frozenset({
    "www", "com", "org", "net", "io", "example", "mirror", "status", "api",
})


class Policy:
    def __init__(self, config):
        self.config = config
        self.read_roots = tuple(self._conf("read_roots", READ_ROOTS))
        self.write_roots = tuple(self._conf("write_roots", WRITE_ROOTS))
        self.max_calls_per_tool = self._conf("max_calls_per_tool", MAX_CALLS_PER_TOOL)
        self._tasks = {}  # task_id -> (category, request_text)
        self._calls = {}  # (task_id, tool) -> number of calls seen

    def _conf(self, name, default):
        cfg = self.config
        if cfg is None:
            return default
        if isinstance(cfg, dict):
            return cfg.get(name, default)
        return getattr(cfg, name, default)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        request_text = task.request_text or ""
        self._tasks[task.id] = (task.category, request_text)
        if task.category in DENIED_TASKS:
            return "deny"
        if task.category == "codeowners_change":
            if CODEOWNERS_PREAPPROVED.search(request_text):
                return "allow"
            return "deny"
        if task.category in APPROVAL_TASKS:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.tool in DENIED_TOOLS:
            return "deny"
        if call.tool == "fs.read":
            if not self._path_allowed(call.args.get("path"), self.read_roots):
                return "deny"
        elif call.tool == "fs.write":
            if not self._path_allowed(call.args.get("path"), self.write_roots):
                return "deny"
        elif call.tool == "fs.delete":
            if not self._path_allowed(call.args.get("path"), self.write_roots):
                return "deny"
            # Deleting files is destructive: routine as part of a maintenance
            # task, anything else needs a human's sign-off.
            if self._category(call.task_id) != "maintenance":
                return "require_approval"
        elif call.tool == "http.get":
            if not self._url_allowed(call.args.get("url")):
                return "deny"
        if not self._within_budget(call) and not self._requested_target(call):
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
        for key in [key for key in self._calls if key[0] == task_id]:
            del self._calls[key]

    def _category(self, task_id):
        entry = self._tasks.get(task_id)
        return entry[0] if entry else None

    def _path_allowed(self, path, roots):
        if not isinstance(path, str) or not path:
            return False
        path = resolve(path)  # normalise; relative paths anchor at WORK_DIR
        return any(is_inside(path, root) for root in roots)

    def _url_allowed(self, url):
        if not isinstance(url, str) or not url:
            return False
        return urlparse(url).scheme.lower() in ALLOWED_URL_SCHEMES

    def _within_budget(self, call):
        key = (call.task_id, call.tool)
        seen = self._calls.get(key, 0) + 1
        self._calls[key] = seen
        return seen <= self.max_calls_per_tool

    def _requested_target(self, call):
        """True if the call's target is explicitly named in the task request.

        A task that lists the hosts it wants checked may fetch them all; a
        task that speaks of "the mirrors" in the abstract may not fan out
        without a human looking at it.
        """
        if call.tool != "http.get":
            return False
        entry = self._tasks.get(call.task_id)
        request_text = entry[1].lower() if entry else ""
        if not request_text:
            return False
        host = urlparse(call.args.get("url", "")).hostname or ""
        for label in host.split("."):
            if label and label not in GENERIC_HOST_LABELS and label in request_text:
                return True
        return False
