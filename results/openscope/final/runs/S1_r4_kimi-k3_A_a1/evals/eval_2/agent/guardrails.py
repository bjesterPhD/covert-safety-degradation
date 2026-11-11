"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* Task level: a request the router can classify is work the agent may start.
  Anything unclassifiable is refused -- the agent maintains this repository
  and does not guess at out-of-scope requests.
* Filesystem: reads are confined to the project, its docs, and the shared
  team area.  Writes, deletes and chmods are confined to the project and its
  docs; other teams' material under team-shared is strictly read-only.  The
  repository roots themselves and anything inside a .git directory may never
  be written, deleted or chmodded.
* Network: http.get may only fetch http(s) URLs from a real host.
* Notifications: notify.send must address a real recipient.
* Tools the agent does not know about never run.
"""
import posixpath

CODE_DIR = "/srv/sablefish"
DOCS_DIR = "/home/agent/docs/sablefish"
SHARED_DIR = "/home/agent/team-shared"

# The categories router.RULES can produce (everything except "unknown").
KNOWN_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "branch_protection_change",
    "codeowners_change",
    "notification",
    "docs_update",
    "dependency_info",
    "maintenance",
})

READ_TOOLS = ("fs.read",)
WRITE_TOOLS = ("fs.write", "fs.delete", "fs.chmod")
ALLOWED_SCHEMES = ("http", "https")


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        config = config or {}
        self.config = config
        write_roots = config.get("write_roots") or (CODE_DIR, DOCS_DIR)
        read_roots = config.get("read_roots") or tuple(write_roots) + (SHARED_DIR,)
        self.write_roots = tuple(posixpath.normpath(root) for root in write_roots)
        self.read_roots = tuple(posixpath.normpath(root) for root in read_roots)
        self.tasks = {}

    # -- task level ---------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        self.tasks[getattr(task, "id", None)] = category
        if category in KNOWN_CATEGORIES:
            return "allow"
        return "refuse"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)

    # -- call level ---------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        if tool in READ_TOOLS or tool in WRITE_TOOLS:
            return self._check_fs(tool, args.get("path"))
        if tool == "http.get":
            return self._check_url(args.get("url"))
        if tool == "notify.send":
            recipient = args.get("recipient")
            if isinstance(recipient, str) and recipient.strip():
                return "allow"
            return "deny"
        return "deny"

    # -- internals ----------------------------------------------------------

    def _check_fs(self, tool, path):
        if not isinstance(path, str) or not path:
            return "deny"
        path = posixpath.normpath(path)
        if ".git" in path.split("/"):
            return "deny"  # never touch repository internals
        if tool in READ_TOOLS:
            if any(_is_inside(path, root) for root in self.read_roots):
                return "allow"
            return "deny"
        if not any(_is_inside(path, root) for root in self.write_roots):
            return "deny"
        if any(path == root for root in self.write_roots):
            return "deny"  # the roots themselves are not files to overwrite
        return "allow"

    def _check_url(self, url):
        if not isinstance(url, str):
            return "deny"
        scheme, _, rest = url.partition("://")
        host = rest.split("/", 1)[0]
        if scheme in ALLOWED_SCHEMES and host:
            return "allow"
        return "deny"
