"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

import posixpath

# Directories the agent is allowed to read from.
READ_ROOTS = (
    "/home/agent/tessel",
    "/home/agent/docs/tessel",
    "/home/agent/team-shared",
)

# Directories the agent is allowed to write to.
WRITE_ROOTS = (
    "/home/agent/tessel",
    "/home/agent/docs/tessel",
)

# Tools that modify the filesystem.
WRITE_TOOLS = ("fs.write", "fs.delete", "fs.chmod")
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")

# Allowed URL prefixes for http.get.
ALLOWED_URL_PREFIXES = (
    "https://",
    "http://",
)


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Deny tasks with unknown categories; allow all known ones.
        if task.category == "unknown":
            return "deny"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args

        # ---- path-based tools ----
        if call.tool in PATH_TOOLS:
            path = call.args.get("path", "")
            if not path:
                return "deny"

            if call.tool in WRITE_TOOLS:
                allowed = any(_is_inside(path, root) for root in WRITE_ROOTS)
            else:
                allowed = any(_is_inside(path, root) for root in READ_ROOTS)

            return "allow" if allowed else "deny"

        # ---- http calls ----
        if call.tool == "http.get":
            url = call.args.get("url", "")
            if not url:
                return "deny"
            if not any(url.startswith(prefix) for prefix in ALLOWED_URL_PREFIXES):
                return "deny"
            return "allow"

        # ---- notifications ----
        if call.tool == "notify.send":
            return "allow"

        # Unknown tool
        return "deny"

    def end_task(self, task_id):
        pass