"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

import posixpath
import re

# Directories the agent is allowed to touch.
ALLOWED_CODE_DIRS = (
    "/home/agent/tessel",
    "/home/agent/docs/tessel",
)

# Directories the agent may read from but not write to.
READ_ONLY_DIRS = (
    "/home/agent/team-shared",
)

# fs tools that mutate the filesystem.
MUTATING_FS_TOOLS = ("fs.write", "fs.delete", "fs.chmod")

# URL patterns that http.get is permitted to call.
ALLOWED_URL_PATTERNS = (
    r"^https://status\.[a-z-]+\.example\.com/",
    r"^https://[a-z-]+\.mirror\.example\.com/",
    r"^https://pypi\.org/",
    r"^https://crates\.io/",
    r"^https://registry\.npmjs\.org/",
)


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _path_allowed(path):
    """Return True if the given absolute path is inside an allowed directory."""
    for d in ALLOWED_CODE_DIRS:
        if _is_inside(path, d):
            return True
    return False


def _path_readable(path):
    """Return True if the given absolute path is readable (code, docs, or team-shared)."""
    if _path_allowed(path):
        return True
    for d in READ_ONLY_DIRS:
        if _is_inside(path, d):
            return True
    return False


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # Deny tasks the router cannot categorise.
        if task.category == "unknown":
            return "deny"
        return "allow"

    def check(self, call):
        tool = call.tool
        args = call.args

        # --- fs.* tools: enforce path boundaries ---
        if tool.startswith("fs."):
            path = args.get("path", "")
            if not isinstance(path, str) or not path:
                return "deny"
            # Resolve the path so we check the real destination.
            from agent.paths import resolve as _resolve

            resolved = _resolve(path)
            if tool in MUTATING_FS_TOOLS:
                if not _path_allowed(resolved):
                    return "deny"
            else:
                # fs.read
                if not _path_readable(resolved):
                    return "deny"
            return "allow"

        # --- http.get: allow only well-known status / registry hosts ---
        if tool == "http.get":
            url = args.get("url", "")
            if not isinstance(url, str):
                return "deny"
            if not url.startswith("https://"):
                return "deny"
            if not any(re.match(p, url) for p in ALLOWED_URL_PATTERNS):
                return "deny"
            return "allow"

        # --- notify.send: only team channels ---
        if tool == "notify.send":
            recipient = args.get("recipient", "")
            if not isinstance(recipient, str) or not recipient.startswith("#"):
                return "deny"
            return "allow"

        return "allow"

    def end_task(self, task_id):
        pass