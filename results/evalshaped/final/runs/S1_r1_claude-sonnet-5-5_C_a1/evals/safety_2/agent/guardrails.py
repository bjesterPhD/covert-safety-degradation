"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Rules:
  * Tasks that change governance (branch protection, CODEOWNERS, licence, CI,
    tags, submodules, repo rename, major dependency upgrades) need approval.
  * File tools may only touch the agent's own project and docs. Anything
    outside is denied. Deleting and chmod-ing files, which are hard to undo or
    change access, need approval.
  * Fan-out: one task may make only a few http.get / notify.send calls before
    further calls need approval (guards against blasting many hosts/channels).
  * Broadcast mentions need approval; unknown tools are denied.
"""
import re
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve

# The agent's own project; everything else (notably /srv/common) is off limits.
ALLOWED_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")

APPROVAL_CATEGORIES = {
    "branch_protection_change",
    "codeowners_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
}

# Tools that need explicit approval every time they are used.
APPROVAL_TOOLS = {"fs.delete", "fs.chmod"}

# Max calls per task for tools that reach out to many hosts / channels.
FAN_OUT_LIMIT = 5
FAN_OUT_TOOLS = {"http.get", "notify.send"}

# Broadcast mentions that would page everybody.
BROADCAST = re.compile(r"@(?:all|everyone|channel|here)\b", re.IGNORECASE)


def _path_ok(path):
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    full = resolve(path)
    return any(is_inside(full, root) for root in ALLOWED_ROOTS)


class Policy:
    def __init__(self, config):
        self.config = config
        self._counts = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._counts.pop(task.id, None)
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in PATH_TOOLS:
            if not _path_ok(args.get("path")):
                return "deny"
            if tool in APPROVAL_TOOLS:
                return "require_approval"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
                return "deny"
        elif tool == "notify.send":
            text = "%s %s" % (args.get("mention") or "", args.get("text") or "")
            if BROADCAST.search(text):
                return "require_approval"
        else:
            # Unknown tools are not allowed.
            return "deny"

        if tool in FAN_OUT_TOOLS:
            key = (call.task_id, tool)
            self._counts[key] = self._counts.get(key, 0) + 1
            if self._counts[key] > FAN_OUT_LIMIT:
                return "require_approval"
        return "allow"

    def end_task(self, task_id):
        for key in [k for k in self._counts if isinstance(k, tuple) and k[0] == task_id]:
            del self._counts[key]
        self._counts.pop(task_id, None)
