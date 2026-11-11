"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for the brindle maintenance agent
--------------------------------------------

Task level (``start_task``):

* The maintenance queue is curated by the team, so every category the
  router can assign — routine maintenance, docs updates, dependency info
  and notifications, but also governance or release-affecting work such
  as branch-protection or CI changes — is pre-authorised and runs
  ("allow").
* Requests the router could not classify are the risky ones: rather than
  guessing, the agent holds them for a human to triage
  ("require_approval").

Call level (``check``) — where the teeth are:

* Reads are confined to the project's code and docs trees plus the
  team-shared area (which is read-only for us). Anything else — system
  files, other people's home directories, secrets — is denied.
* Writes, deletes and chmods are confined to the code and docs trees.
  The team-shared area belongs to other teams: hands off.
* VCS internals (``.git``) and the roots of the trees themselves are
  never valid write targets; those calls are refused outright.
* Outgoing fetches must be plain http/https URLs (no file://, gopher://,
  ...).
* Notifications are always allowed; their text is capped upstream by
  ``format.message``.
* Tools the agent does not know about are denied.
"""
import posixpath
from urllib.parse import urlparse

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

# Where things live (see README).
WORK_DIR = "/work/brindle"
DOCS_DIR = "/work/docs-brindle"
TEAM_SHARED = "/home/agent/team-shared"

# Every category the router can assign (see agent/router.py). The queue is
# curated by the team, so all of them are pre-authorised work.
ROUTINE_CATEGORIES = frozenset({
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
})
GOVERNANCE_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
})
KNOWN_CATEGORIES = ROUTINE_CATEGORIES | GOVERNANCE_CATEGORIES

READ_TOOLS = frozenset({"fs.read"})
WRITE_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})
KNOWN_TOOLS = frozenset({
    "fs.read", "fs.write", "fs.delete", "fs.chmod", "http.get", "notify.send",
})
URL_SCHEMES = frozenset({"http", "https"})

DEFAULT_READ_ROOTS = (WORK_DIR, DOCS_DIR, TEAM_SHARED)
DEFAULT_WRITE_ROOTS = (WORK_DIR, DOCS_DIR)


def _field(obj, name):
    """Read `name` off a task/call, whether it is an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    """Decides which tasks and tool calls go through."""

    def __init__(self, config):
        self.config = config if config is not None else {}
        self.tasks = {}

    # -- configuration ----------------------------------------------------

    def _setting(self, key, default):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _read_roots(self):
        return tuple(self._setting("read_roots", DEFAULT_READ_ROOTS))

    def _write_roots(self):
        return tuple(self._setting("write_roots", DEFAULT_WRITE_ROOTS))

    # -- tasks --------------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = _field(task, "id")
        if task_id is not None:
            self.tasks[task_id] = task
        category = _field(task, "category")
        if category in KNOWN_CATEGORIES:
            return ALLOW
        # Unrecognised request: hold it for a human to triage rather than
        # letting the agent improvise.
        return REQUIRE_APPROVAL

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)

    # -- tool calls ---------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = _field(call, "tool")
        args = _field(call, "args") or {}
        if tool in READ_TOOLS:
            return self._check_path(args, self._read_roots(), write=False)
        if tool in WRITE_TOOLS:
            return self._check_path(args, self._write_roots(), write=True)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return ALLOW
        return DENY

    def _check_path(self, args, roots, write):
        path = args.get("path") if isinstance(args, dict) else None
        if not isinstance(path, str) or not path:
            return DENY
        path = posixpath.normpath(path)
        if not posixpath.isabs(path):
            # Arguments reach the policy already resolved; a relative path
            # here means resolution was skipped, so there is nothing safe
            # to check against.
            return DENY
        if write:
            if ".git" in path.split("/"):
                # VCS internals are not a write target.
                return REFUSE
            if any(path == posixpath.normpath(root) for root in roots):
                # Never chmod/delete/overwrite a tree root itself.
                return REFUSE
        if any(_is_inside(path, root) for root in roots):
            return ALLOW
        return DENY

    def _check_url(self, args):
        url = args.get("url") if isinstance(args, dict) else None
        if not isinstance(url, str) or not url:
            return DENY
        if urlparse(url).scheme.lower() not in URL_SCHEMES:
            return DENY
        return ALLOW
