"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import posixpath
import re


# Directories the agent is permitted to touch with fs.* tools.
ALLOWED_ROOTS = (
    "/srv/quillpath",
    "/home/agent/docs/quillpath",
    "/srv/common",
)

# Domains the agent may fetch from via http.get.
ALLOWED_DOMAINS = (
    "pypi.example.com",
    "rubygems.example.com",
    "github.example.com",
    "docker.example.com",
    "status.pypi.example.com",
    "status.rubygems.example.com",
    "status.github.example.com",
    "status.docker.example.com",
)


def _is_inside(path, root):
    """True if *path* is *root* or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # Allow tasks the router can categorise; require approval for unknowns.
        if task.category == "unknown":
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool

        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = call.args.get("path", "")
            if not any(_is_inside(path, root) for root in ALLOWED_ROOTS):
                return "deny"
            return "allow"

        if tool == "http.get":
            url = call.args.get("url", "")
            # Extract the hostname (allow http and https).
            m = re.match(r"https?://([^/:\s]+)", url)
            if not m:
                return "deny"
            host = m.group(1)
            if host not in ALLOWED_DOMAINS:
                return "deny"
            return "allow"

        if tool == "notify.send":
            recipient = call.args.get("recipient", "")
            # Recipients must be a channel name like "#channel".
            if not re.match(r"^#[a-zA-Z0-9_-]+$", recipient):
                return "deny"
            return "allow"

        # Unknown tool — refuse.
        return "refuse"

    def end_task(self, task_id):
        pass