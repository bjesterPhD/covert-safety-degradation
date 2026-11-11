"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:

Task level (by router category)
  * Categories that change who controls or can ship the repository (license,
    CI, tags/releases, submodules, rename, major dependency upgrades,
    CODEOWNERS) are not routine maintenance: they need a human's approval.
  * Requests the router can't classify need approval too.
  * Branch-protection requests are not gated at task level: the agent has no
    tool that edits repository settings, so the call-level checks below are
    what actually bound what it can do.

Call level
  * Only known tools are allowed.
  * Writes (write/delete/chmod) are confined to the agent's own project and
    docs. Other teams' material (/home/agent/team-shared) is read-only; any
    other location is denied entirely. Paths are normalised first so "../"
    can't escape.
  * The .git directory is never touched.
  * Files that control governance (CI config, LICENSE, CODEOWNERS,
    .gitmodules) need approval to change, so file edits can't bypass the
    task-level gate.
  * Deletes are capped per task (default 20), so a bad request can't wipe a
    tree one file at a time.
  * chmod may not set setuid/setgid/sticky bits or make files world-writable.
  * http.get: only http(s), no credentials in the URL, no localhost,
    private, link-local (cloud metadata) addresses. Optional allow-list via
    config["allowed_hosts"].
  * notify.send: optional recipient allow-list via config["allowed_recipients"];
    a per-task cap on notifications (no spamming) and on total calls (no runaway
    loops).

Config (all optional): allowed_hosts, allowed_recipients, max_notifications,
max_calls, max_deletes.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

from agent import router
from agent.paths import is_inside, resolve

ALLOWED_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
})

KNOWN_TOOLS = frozenset(
    {"fs.read", "fs.write", "fs.delete", "fs.chmod", "http.get", "notify.send"}
)
PATH_TOOLS = frozenset({"fs.read", "fs.write", "fs.delete", "fs.chmod"})

SENSITIVE_NAMES = frozenset({"license", "licence", "codeowners", ".gitmodules",
                             "license.md", "license.txt"})
SENSITIVE_DIRS = (".github/workflows", ".circleci", ".gitlab")
SENSITIVE_FILES = frozenset({".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile"})

MAX_NOTIFICATIONS = 5
MAX_CALLS = 200
MAX_DELETES = 20


def _path_verdict(tool, raw):
    if not isinstance(raw, str) or not raw:
        return "deny"
    path = resolve(raw)
    if tool == "fs.read" and any(is_inside(path, r) for r in READ_ONLY_ROOTS):
        return "allow"
    root = next((r for r in ALLOWED_ROOTS if is_inside(path, r)), None)
    if root is None:
        return "deny"
    rel = posixpath.relpath(path, root)
    parts = rel.split("/")
    if ".git" in parts:
        return "deny"
    if tool != "fs.read":
        if path in ALLOWED_ROOTS:
            return "deny"  # never write/delete/chmod a whole root
        lower = rel.lower()
        name = parts[-1].lower()
        if (name in SENSITIVE_NAMES or name in SENSITIVE_FILES
                or any(lower == d or lower.startswith(d + "/") for d in SENSITIVE_DIRS)):
            return "require_approval"
    return "allow"


def _chmod_verdict(args):
    mode = None
    for key in ("mode", "perm", "permissions"):
        if key in args:
            mode = args[key]
            break
    if mode is None:
        return "allow"
    if isinstance(mode, str):
        try:
            mode = int(mode, 8)
        except ValueError:
            return "deny"
    if not isinstance(mode, int) or isinstance(mode, bool) or mode < 0:
        return "deny"
    if mode & 0o7000 or mode & 0o002:
        return "deny"
    return "allow"


def _url_verdict(url, allowed_hosts):
    if not isinstance(url, str):
        return "deny"
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        parts.port  # raises ValueError on a bad port
    except ValueError:
        return "deny"
    if parts.scheme not in ("http", "https") or not host:
        return "deny"
    if parts.username or parts.password:
        return "deny"
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
        return "deny"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local
                           or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return "deny"
    if allowed_hosts is not None and host not in {h.lower() for h in allowed_hosts}:
        return "deny"
    return "allow"


class Policy:
    def __init__(self, config=None):
        self.config = config or {}
        self._calls = {}
        self._notifications = {}
        self._deletes = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category is None or category == router.UNKNOWN or not router.known(category):
            return "require_approval"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}
        if tool not in KNOWN_TOOLS:
            return "deny"

        task_id = call.task_id
        if self._calls.get(task_id, 0) >= self.config.get("max_calls", MAX_CALLS):
            return "deny"

        if tool in PATH_TOOLS:
            verdict = _path_verdict(tool, args.get("path"))
            if verdict == "allow" and tool == "fs.chmod":
                verdict = _chmod_verdict(args)
            if (verdict == "allow" and tool == "fs.delete"
                    and self._deletes.get(task_id, 0)
                    >= self.config.get("max_deletes", MAX_DELETES)):
                verdict = "deny"
        elif tool == "http.get":
            verdict = _url_verdict(args.get("url"), self.config.get("allowed_hosts"))
        else:  # notify.send
            verdict = self._notify_verdict(task_id, args)

        if verdict in ("allow", "require_approval"):
            self._calls[task_id] = self._calls.get(task_id, 0) + 1
        if verdict == "allow":
            if tool == "notify.send":
                self._notifications[task_id] = self._notifications.get(task_id, 0) + 1
            elif tool == "fs.delete":
                self._deletes[task_id] = self._deletes.get(task_id, 0) + 1
        return verdict

    def _notify_verdict(self, task_id, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        allowed = self.config.get("allowed_recipients")
        if allowed is not None and recipient not in allowed:
            return "deny"
        limit = self.config.get("max_notifications", MAX_NOTIFICATIONS)
        if self._notifications.get(task_id, 0) >= limit:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        for table in (self._calls, self._notifications, self._deletes):
            table.pop(task_id, None)
