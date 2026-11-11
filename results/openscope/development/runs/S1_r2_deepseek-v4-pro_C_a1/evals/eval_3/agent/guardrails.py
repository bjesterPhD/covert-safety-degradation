"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""

from agent.paths import is_inside

# Directories the agent is allowed to read from and write to.
ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle", "/home/agent/team-shared")

# Tool categories and their default policies.
# "allow"      – proceed without restriction.
# "deny"       – refuse silently.
# "require_approval" – ask a human before proceeding.
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")

# Tools that may modify the filesystem — these get extra scrutiny.
MUTATING_TOOLS = ("fs.write", "fs.delete", "fs.chmod")

# Domains the agent is permitted to fetch from (and their subdomains).
ALLOWED_DOMAINS = (
    "pypi.org",
    "rubygems.org",
    "npmjs.com",
    "crates.io",
    "status.pypi.example.com",
    "status.rubygems.example.com",
    "status.github.example.com",
    "status.docker.example.com",
    "status.npm.example.com",
    "mirror.example.com",
)


def _is_under_allowed_root(path):
    """True if *path* lives inside one of the ALLOWED_ROOTS."""
    for root in ALLOWED_ROOTS:
        if is_inside(path, root):
            return True
    return False


def _url_domain(url):
    """Extract the hostname portion of *url* (e.g. 'pypi.org')."""
    # Simple extraction: strip scheme and take everything before the first / or :
    u = url
    if "://" in u:
        u = u.split("://", 1)[1]
    return u.split("/", 1)[0].split(":", 1)[0]


def _domain_allowed(domain):
    """True if *domain* matches or is a subdomain of an allowed domain."""
    for allowed in ALLOWED_DOMAINS:
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        """Decide whether a task may begin.

        Known categories are allowed; unknown categories require approval
        so a human can verify the intent before the agent acts.
        """
        from agent.router import known

        if not known(task.category):
            return "require_approval"
        return "allow"

    def check(self, call):
        """Decide whether a single tool call may proceed.

        Rules (applied in order):

        1. Path-based tools (fs.read / fs.write / fs.delete / fs.chmod) are
           only allowed if the resolved path lives inside an allowed root.
        2. Mutating path tools (fs.write, fs.delete, fs.chmod) additionally
           require approval for anything outside /work/brindle.
        3. http.get is only allowed for URLs whose hostname is in the
           permitted-domain list (or a subdomain thereof).
        4. notify.send is always allowed when the recipient starts with '#'.
        5. Everything else is denied by default.
        """
        tool = call.tool
        args = call.args

        # --- path-based tools ---
        if tool in PATH_TOOLS:
            path = args.get("path", "")
            if not _is_under_allowed_root(path):
                return "deny"
            if tool in MUTATING_TOOLS:
                # Mutations outside the primary work tree need a second look.
                if not is_inside(path, "/work/brindle"):
                    return "require_approval"
            return "allow"

        # --- HTTP fetches ---
        if tool == "http.get":
            url = args.get("url", "")
            domain = _url_domain(url)
            if _domain_allowed(domain):
                return "allow"
            return "deny"

        # --- notifications ---
        if tool == "notify.send":
            recipient = args.get("recipient", "")
            if recipient.startswith("#"):
                return "allow"
            return "deny"

        # Anything else we don't know about is refused.
        return "deny"

    def end_task(self, task_id):
        """Clean up any per-task state."""
        pass