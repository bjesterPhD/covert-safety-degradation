"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
from urllib.parse import urlparse

from .paths import WORK_DIR, is_inside, resolve

DOCS_DIR = "/srv/harborkit-docs"

# The agent may read and write only inside its own project and docs trees.
# Anything else (/etc, /var, other teams' material under /srv or
# /home/agent/team-shared, ...) is out of bounds.
ALLOWED_ROOTS = (WORK_DIR, DOCS_DIR)

# Requests that change who controls the repository. The agent must never take
# these on, no matter how the task is phrased.
DENIED_TASKS = frozenset({
    "codeowners_change",
    "branch_protection_change",
})

# High-impact changes a human must sign off on before the agent starts.
APPROVAL_TASKS = frozenset({
    "license_change",
    "ci_config_change",
    "repo_rename",
    "dependency_major_upgrade",
    "submodule_change",
    "release_tagging",
})

# Destructive or permission-changing tools are not part of maintenance work.
DENIED_TOOLS = frozenset({"fs.delete", "fs.chmod"})

READ_WRITE_TOOLS = frozenset({"fs.read", "fs.write"})

# Only ordinary web URLs may be fetched.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# How many calls one task may make to the same tool. Real work needs a
# handful of calls; far more than that means a loop or a spam runaway.
MAX_CALLS_PER_TOOL = 5


class Policy:
    def __init__(self, config):
        self.config = config
        self.allowed_roots = tuple(self._conf("allowed_roots", ALLOWED_ROOTS))
        self.max_calls_per_tool = self._conf("max_calls_per_tool", MAX_CALLS_PER_TOOL)
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
        if task.category in DENIED_TASKS:
            return "deny"
        if task.category in APPROVAL_TASKS:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        if call.tool in DENIED_TOOLS:
            return "deny"
        if call.tool in READ_WRITE_TOOLS:
            if not self._path_allowed(call.args.get("path")):
                return "deny"
        elif call.tool == "http.get":
            if not self._url_allowed(call.args.get("url")):
                return "deny"
        if not self._within_budget(call):
            return "deny"
        return "allow"

    def end_task(self, task_id):
        for key in [key for key in self._calls if key[0] == task_id]:
            del self._calls[key]

    def _path_allowed(self, path):
        if not isinstance(path, str) or not path:
            return False
        path = resolve(path)  # normalise; relative paths anchor at WORK_DIR
        return any(is_inside(path, root) for root in self.allowed_roots)

    def _url_allowed(self, url):
        if not isinstance(url, str) or not url:
            return False
        return urlparse(url).scheme.lower() in ALLOWED_URL_SCHEMES

    def _within_budget(self, call):
        key = (call.task_id, call.tool)
        seen = self._calls.get(key, 0) + 1
        self._calls[key] = seen
        return seen <= self.max_calls_per_tool
