"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

import os
import posixpath

# Directories the agent is allowed to read and write.
ALLOWED_ROOTS = [
    "/srv/quillpath",
    "/home/agent/docs/quillpath",
]

# Destructive tools that warrant extra scrutiny.
DESTRUCTIVE_TOOLS = {"fs.delete", "fs.chmod"}

# Allowed notification channels.
ALLOWED_CHANNELS = {
    "#frontend",
    "#backend",
    "#docs",
    "#triage",
    "#infra",
    "#general",
}

# Allowed HTTP endpoints (prefix match).
ALLOWED_URL_PREFIXES = [
    "https://status.pypi.example.com/",
    "https://status.rubygems.example.com/",
    "https://status.github.example.com/",
    "https://status.docker.example.com/",
    "https://pypi.org/",
    "https://rubygems.org/",
    "https://api.github.com/",
    "https://registry.npmjs.org/",
]


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _path_allowed(path):
    """True if `path` is inside one of the allowed roots."""
    resolved = os.path.realpath(path)
    return any(_is_inside(resolved, posixpath.normpath(r)) for r in ALLOWED_ROOTS)


def _url_allowed(url):
    """True if `url` starts with one of the allowed prefixes."""
    return any(url.startswith(prefix) for prefix in ALLOWED_URL_PREFIXES)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task of this category may begin.

        Unknown categories are refused. All known categories are allowed.
        """
        from agent.router import known

        if not known(task.category) or task.category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        """Decide whether a specific tool call may proceed.

        - fs.* tools must target paths inside allowed roots.
        - fs.delete and fs.chmod require approval.
        - http.get must target an allowed URL prefix.
        - notify.send must target an allowed channel.
        """
        tool = call.tool

        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = call.args.get("path", "")
            if not isinstance(path, str) or not _path_allowed(path):
                return "refuse"
            if tool in DESTRUCTIVE_TOOLS:
                return "require_approval"

        if tool == "http.get":
            url = call.args.get("url", "")
            if not isinstance(url, str) or not _url_allowed(url):
                return "refuse"

        if tool == "notify.send":
            recipient = call.args.get("recipient", "")
            if not isinstance(recipient, str) or recipient not in ALLOWED_CHANNELS:
                return "refuse"

        return "allow"

    def end_task(self, task_id):
        pass