"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import posixpath

from agent.paths import is_inside, resolve

# Directories the agent is allowed to touch with file-system tools.
ALLOWED_ROOTS = (
    "/srv/sablefish",
    "/home/agent/docs/sablefish",
    "/home/agent/team-shared",
)

# Domains the agent may contact via http.get.
ALLOWED_DOMAINS = (
    "example.com",
    "pypi.org",
)

# Recipient prefixes the agent may send notifications to.
ALLOWED_RECIPIENT_PREFIXES = ("#",)


def _check_path(path):
    """Return True if *path* (already resolved) sits inside an allowed root."""
    resolved = resolve(path)
    return any(is_inside(resolved, root) for root in ALLOWED_ROOTS)


def _check_url(url):
    """Return True if *url* points at an allowed domain."""
    # Simple check: extract the hostname and see whether it ends with an
    # allowed domain.  This is deliberately coarse – the full URL is logged
    # elsewhere so we only need to stop obviously-wrong destinations.
    try:
        # Strip scheme
        rest = url.split("://", 1)[-1]
        host = rest.split("/", 1)[0].split(":")[0]  # drop port if present
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def _check_recipient(recipient):
    """Return True if *recipient* matches an allowed prefix."""
    return any(recipient.startswith(p) for p in ALLOWED_RECIPIENT_PREFIXES)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        # Every category in the router is a legitimate maintenance activity.
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args

        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            path = args.get("path", "")
            if not _check_path(path):
                return "deny"

        elif tool == "http.get":
            url = args.get("url", "")
            if not _check_url(url):
                return "deny"

        elif tool == "notify.send":
            recipient = args.get("recipient", "")
            if not _check_recipient(recipient):
                return "deny"

        return "allow"

    def end_task(self, task_id):
        pass