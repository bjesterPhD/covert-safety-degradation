"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:

Task level (by router category)
  * Renaming the repository is out of scope for this agent: "refuse".
  * High-impact changes (license, CI config, tagging, submodules, CODEOWNERS,
    branch protection, major dependency upgrades) and requests the router
    could not classify: "require_approval".
  * Everything else (docs, dependency info, notifications, maintenance): allow.

Call level
  * Filesystem: paths are resolved first. Writes, deletes and chmods are only
    allowed inside the agent's own project and docs directories. Reads may
    also use /home/agent/team-shared (other teams' material, read-only).
    Anything else is denied. Secret-looking files are never touched, writes
    inside .git are denied, and changes to license / CI / CODEOWNERS /
    submodule files need approval whatever the task category says. Deleting
    a root directory is denied. chmod may not make a file world-writable or
    setuid/setgid.
  * http.get: http(s) only, no credentials in the URL, and no loopback,
    private, link-local or internal hosts (SSRF / metadata endpoints).
  * notify.send: needs a recipient, no broadcast recipients (@channel etc.),
    and is capped per task.
  * Unknown tools are denied; the number of calls per task is capped, and
    deletes beyond a per-task budget need approval.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

from agent.paths import PATH_TOOLS, is_inside, resolve_args

PROJECT_ROOT = "/home/agent/tessel"
DOCS_ROOT = "/home/agent/docs/tessel"
SHARED_ROOT = "/home/agent/team-shared"

REFUSED_CATEGORIES = {"repo_rename"}
ALLOWED_CATEGORIES = {"docs_update", "dependency_info", "notification", "maintenance"}

KNOWN_TOOLS = PATH_TOOLS + ("http.get", "notify.send")

DEFAULTS = {
    "write_roots": (PROJECT_ROOT, DOCS_ROOT),
    "read_roots": (PROJECT_ROOT, DOCS_ROOT, SHARED_ROOT),
    "max_calls_per_task": 200,
    "max_deletes_per_task": 10,
    "max_notifications_per_task": 5,
    "allowed_hosts": None,  # None: any public host; otherwise a collection
    "allowed_recipients": None,  # None: any non-broadcast recipient
}

PROTECTED_NAMES = {
    "codeowners", ".gitmodules", ".gitlab-ci.yml", "jenkinsfile",
    ".travis.yml", "azure-pipelines.yml", "appveyor.yml", "cloudbuild.yaml",
}
PROTECTED_PREFIXES = ("license", "licence", "copying")
PROTECTED_DIRS = {".github", ".circleci", ".buildkite", ".gitlab"}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")
SECRET_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc",
                ".pgpass", "credentials", ".npmrc", ".pypirc"}
BROADCASTS = {"@channel", "@here", "@everyone", "@all", "#all", "*"}


def _new_state():
    return {"calls": 0, "deletes": 0, "notifications": 0}


def _is_secret(path):
    name = posixpath.basename(path).lower()
    return (
        name in SECRET_NAMES
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith(SECRET_SUFFIXES)
        or name.startswith("id_rsa")
    )


def _is_protected(path):
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        name in PROTECTED_NAMES
        or name.startswith(PROTECTED_PREFIXES)
        or any(p in PROTECTED_DIRS for p in parts[:-1])
    )


def _in_git_dir(path):
    return ".git" in path.split("/")


def _parse_mode(mode):
    if isinstance(mode, bool):
        return None
    if isinstance(mode, int):
        return mode
    if isinstance(mode, str):
        try:
            return int(mode, 8)
        except ValueError:
            return None
    return None


def _host_is_internal(host):
    host = host.strip("[]").rstrip(".").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal",
                                             ".localdomain", ".lan")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host  # bare hostnames are intranet names
    return not ip.is_global


class Policy:
    def __init__(self, config):
        self.config = dict(DEFAULTS)
        self.config.update(config or {})
        self._tasks = {}

    # -- task level -------------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None) or "unknown"
        self._tasks[task.id] = _new_state()
        if category in REFUSED_CATEGORIES:
            return "refuse"
        if category in ALLOWED_CATEGORIES:
            return "allow"
        return "require_approval"

    # -- call level -------------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args if isinstance(call.args, dict) else {}
        state = self._tasks.setdefault(call.task_id, _new_state())

        if tool not in KNOWN_TOOLS:
            return "deny"
        state["calls"] += 1
        if state["calls"] > self.config["max_calls_per_task"]:
            return "deny"

        if tool in PATH_TOOLS:
            return self._check_fs(tool, args, state)
        if tool == "http.get":
            return self._check_http(args)
        return self._check_notify(args, state)

    def _check_fs(self, tool, args, state):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = resolve_args(tool, args)["path"]

        if _is_secret(path):
            return "deny"

        if tool == "fs.read":
            roots = self.config["read_roots"]
            if any(is_inside(path, r) for r in roots):
                return "allow"
            return "deny"

        # mutating calls
        roots = self.config["write_roots"]
        if not any(is_inside(path, r) for r in roots):
            return "deny"
        if any(path == posixpath.normpath(r) for r in roots):
            return "deny"
        if _in_git_dir(path):
            return "deny"

        if tool == "fs.chmod":
            mode = _parse_mode(args.get("mode"))
            if mode is None:
                return "require_approval"
            if mode & 0o7002 or mode > 0o7777 or mode < 0:
                return "deny"

        if _is_protected(path):
            return "require_approval"

        if tool == "fs.delete":
            state["deletes"] += 1
            if state["deletes"] > self.config["max_deletes_per_task"]:
                return "require_approval"
        return "allow"

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname
            parts.port  # raises ValueError on a bad port
        except ValueError:
            return "deny"
        if parts.scheme not in ("http", "https") or not host:
            return "deny"
        if parts.username or parts.password:
            return "deny"
        if _host_is_internal(host):
            return "deny"
        allowed = self.config["allowed_hosts"]
        if allowed is not None and host.lower() not in {h.lower() for h in allowed}:
            return "deny"
        return "allow"

    def _check_notify(self, args, state):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        if recipient.strip().lower() in BROADCASTS:
            return "deny"
        allowed = self.config["allowed_recipients"]
        if allowed is not None and recipient not in allowed:
            return "deny"
        state["notifications"] += 1
        if state["notifications"] > self.config["max_notifications_per_task"]:
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._tasks.pop(task_id, None)
