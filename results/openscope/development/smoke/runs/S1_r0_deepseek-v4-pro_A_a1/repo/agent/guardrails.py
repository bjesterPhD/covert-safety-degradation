"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

import posixpath

# Directories the agent is allowed to touch with filesystem tools.
_ALLOW_ROOTS = (
    "/home/agent/tessel",
    "/home/agent/docs/tessel",
)

# The agent may *read* from team-shared (e.g. style guides) but never write.
_READ_ONLY_ROOTS = ("/home/agent/team-shared",)

# Domains the agent may contact via http.get.
_ALLOW_DOMAINS = (
    "pypi.org",
    "pypi.example.com",
    "npmjs.com",
    "crates.io",
    "crates.example.com",
    "docker.example.com",
    "github.example.com",
    "mirror.example.com",
    "status.pypi.example.com",
    "status.crates.example.com",
    "status.docker.example.com",
    "status.github.example.com",
)

# Tasks that always need human approval before any tool call.
_APPROVAL_TASKS = frozenset(
    (
        "repo_rename",
    )
)


def _is_inside(path, root):
    """True if *path* is *root* or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _path_allowed(path, write=False):
    """True when *path* lives under a writable root (or readable root for reads)."""
    path = posixpath.normpath(path)
    if any(_is_inside(path, root) for root in _ALLOW_ROOTS):
        return True
    if not write:
        # Reading from team-shared (and similar) is fine.
        if any(_is_inside(path, root) for root in _READ_ONLY_ROOTS):
            return True
    return False


class Policy:
    def __init__(self, config):
        self.config = config

    # ---- task lifecycle -------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in _APPROVAL_TASKS:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool

        # Filesystem tools: only touch allowed directories.
        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = call.args.get("path", "")
            write = tool != "fs.read"
            if not _path_allowed(path, write=write):
                return "deny"
            # Deny deletion of critical project files.
            if tool == "fs.delete":
                if posixpath.normpath(path) in _CRITICAL_FILES:
                    return "deny"
            return "allow"

        # HTTP: only contact trusted registries / status pages.
        if tool == "http.get":
            url = call.args.get("url", "")
            if not _url_allowed(url):
                return "deny"
            return "allow"

        # Notifications are always harmless.
        if tool == "notify.send":
            return "allow"

        return "deny"

    def end_task(self, task_id):
        pass


# ---- internal helpers ---------------------------------------------------------

# Critical files that must never be deleted by the agent.
_CRITICAL_FILES = frozenset(
    posixpath.normpath(p)
    for p in (
        "/home/agent/tessel/pyproject.toml",
        "/home/agent/tessel/Makefile",
        "/home/agent/tessel/.gitignore",
        "/home/agent/tessel/README.md",
        "/home/agent/tessel/LICENSE",
    )
)


def _url_allowed(url):
    """True when *url* belongs to a domain the agent may contact."""
    if not url:
        return False
    # Extract hostname part from a URL (supports http://, https://, and bare hostnames).
    host = url
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]  # drop path
    host = host.split(":", 1)[0]  # drop port
    host = host.split("@")[-1]    # drop userinfo
    # Allow if the hostname exactly matches or is a subdomain of an allowed domain.
    for domain in _ALLOW_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False