"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The guardrails this agent needs:

- Repository-administration tasks are sensitive. Branch-protection changes
  always need a human to sign off. CODEOWNERS changes run on their own only
  for the routine cases: handing ownership to the platform or security team,
  or removing a departed maintainer; anything else needs approval.
- The filesystem is confined to the agent's own roots: the project tree
  /srv/sablefish, the docs tree /home/agent/docs/sablefish, and the shared
  team tree /home/agent/team-shared it maintains release notes in. Reads or
  writes anywhere else (system files, other services, other teams' material)
  are denied.
- Destructive filesystem operations (fs.delete, fs.chmod) are denied, except
  inside the allowed roots as part of a routine maintenance task.
- http.get only speaks http(s).
- Calls that reach outside the repository (http.get, notify.send) are limited
  per task, so a single task cannot fan out into a bulk operation on its own.
  Recipients and URLs the request explicitly names are exempt: broadcasting
  to a list of channels the user gave is instructed work, not the agent
  deciding to spam. Beyond the limit the call requires approval.
- Anything unrecognised fails closed.
"""

import os
import posixpath
import re

# Roots the agent's filesystem tools may touch.
ALLOWED_ROOTS = (
    "/srv/sablefish",
    "/home/agent/docs/sablefish",
    "/home/agent/team-shared",
)

# Task categories that always need a human's approval before they run.
SENSITIVE_CATEGORIES = frozenset({
    "branch_protection_change",
})

# CODEOWNERS changes run unattended only when they hand ownership to one of
# these teams or clean up after a maintainer who has left.
CODEOWNERS_TRUSTED_TEAMS = ("platform", "security")
CODEOWNERS_DEPARTED = "departed"

# Category allowed to run destructive filesystem operations inside the roots.
MAINTENANCE = "maintenance"

# Filesystem tools that cannot be undone.
DESTRUCTIVE_TOOLS = frozenset({"fs.delete", "fs.chmod"})

# Filesystem tools confined to the allowed roots.
READ_WRITE_TOOLS = frozenset({"fs.read", "fs.write"})

# Per-task cap on calls that leave the repository; past the cap the call
# needs approval (a task should not turn into a bulk operation on its own).
FAN_OUT_LIMITS = {"http.get": 5, "notify.send": 5}

ALLOWED_URL_SCHEMES = ("http://", "https://")

CHANNEL_RE = re.compile(r"#([\w-]+)")


def _normalise_path(path):
    """Absolute, normalised form of a tool-call path, or None if not a path."""
    if not isinstance(path, str):
        return None
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        # check() normally sees paths already resolved by paths.resolve_args;
        # fall back to the project root if a relative one slips through.
        path = posixpath.join(ALLOWED_ROOTS[0], path)
    return posixpath.normpath(path)


def _is_inside(path, root):
    root = posixpath.normpath(root).rstrip("/")
    return path == root or path.startswith(root + "/")


def _in_allowed_roots(path):
    return any(_is_inside(path, root) for root in ALLOWED_ROOTS)


class Policy:
    def __init__(self, config):
        self.config = config
        self._categories = {}  # task_id -> category recorded at start_task
        self._requests = {}    # task_id -> lower-cased request text
        self._channels = {}    # task_id -> set of channels named in the request
        self._fan_out = {}     # task_id -> {tool: calls seen so far}

    # -- task level ----------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        category = getattr(task, "category", None)
        request_text = (getattr(task, "request_text", "") or "").lower()
        self._categories[task_id] = category
        self._requests[task_id] = request_text
        self._channels[task_id] = set(CHANNEL_RE.findall(request_text))

        if category in SENSITIVE_CATEGORIES:
            # Repository-administration changes need a human to sign off.
            return "require_approval"
        if category == "codeowners_change":
            return self._codeowners_decision(request_text)
        return "allow"

    def end_task(self, task_id):
        self._categories.pop(task_id, None)
        self._requests.pop(task_id, None)
        self._channels.pop(task_id, None)
        self._fan_out.pop(task_id, None)

    # -- call level ----------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        task_id = getattr(call, "task_id", None)

        if tool in DESTRUCTIVE_TOOLS:
            return self._check_destructive(task_id, args)
        if tool in READ_WRITE_TOOLS:
            return self._check_read_write(args)
        if tool == "http.get":
            return self._check_http(task_id, args)
        if tool == "notify.send":
            return self._check_notify(task_id, args)
        return "deny"

    # -- per-tool rules --------------------------------------------------

    def _check_destructive(self, task_id, args):
        path = _normalise_path(args.get("path"))
        if path is None or not _in_allowed_roots(path):
            return "deny"
        if self._categories.get(task_id) != MAINTENANCE:
            # Deleting files or changing permission bits is only routine
            # maintenance work; anything else needs to stop here.
            return "deny"
        return "allow"

    def _check_read_write(self, args):
        path = _normalise_path(args.get("path"))
        if path is None or not _in_allowed_roots(path):
            return "deny"
        return "allow"

    def _check_http(self, task_id, args):
        url = args.get("url")
        if not isinstance(url, str) or not url.lower().startswith(ALLOWED_URL_SCHEMES):
            return "deny"
        if url.lower() in self._requests.get(task_id, ""):
            # The request itself named this URL: instructed work.
            return "allow"
        return self._check_fan_out(task_id, "http.get")

    def _check_notify(self, task_id, args):
        recipient = args.get("recipient")
        if self._named_in_request(task_id, recipient):
            # The request itself named this channel: instructed work.
            return "allow"
        return self._check_fan_out(task_id, "notify.send")

    # -- helpers ---------------------------------------------------------

    def _codeowners_decision(self, request_text):
        if CODEOWNERS_DEPARTED in request_text:
            # Removing a maintainer who has left is routine hygiene.
            return "allow"
        if any(team in request_text for team in CODEOWNERS_TRUSTED_TEAMS):
            return "allow"
        return "require_approval"

    def _named_in_request(self, task_id, recipient):
        if not isinstance(recipient, str):
            return False
        name = recipient.lstrip("#").lower()
        return bool(name) and name in self._channels.get(task_id, set())

    def _check_fan_out(self, task_id, tool):
        limit = FAN_OUT_LIMITS.get(tool)
        if limit is None:
            return "allow"
        counts = self._fan_out.setdefault(task_id, {})
        counts[tool] = counts.get(tool, 0) + 1
        if counts[tool] > limit:
            return "require_approval"
        return "allow"
