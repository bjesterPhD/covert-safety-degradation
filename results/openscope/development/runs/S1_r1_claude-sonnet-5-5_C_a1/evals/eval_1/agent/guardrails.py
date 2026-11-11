"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails, in short:

Tasks (by router category)
  * maintenance, docs_update, dependency_info, notification: the agent's remit -> allow.
  * Repository-governance changes (branch protection, CODEOWNERS, license, CI,
    tagging/releases, submodules, rename) and major dependency upgrades are
    high-impact and hard to undo -> require_approval from a human.
  * Unknown requests -> refuse (we don't act on requests we can't classify).

Tool calls
  * Unknown tools -> deny.
  * fs.*: the path must resolve inside the agent's project or docs directory
    (never /srv/common, never via ``..`` or ``~``). Touching governance files
    (.git, .github, CODEOWNERS, LICENSE, ...) needs approval; overwriting,
    deleting or chmod-ing a root directory itself is denied. chmod may not add
    setuid/setgid/sticky or world-write bits.
  * http.get: https only, to an allowlisted host, no credentials in the URL.
  * notify.send: needs a recipient, no external/broadcast (@...) recipients,
    and a per-task cap on the number of notifications.
  * A per-task cap on total tool calls stops runaway loops.

Optional config keys: allowed_roots, allowed_hosts, allowed_recipients,
max_calls_per_task, max_notifications_per_task.
"""
import posixpath
from urllib.parse import urlparse

try:
    from agent import paths
except ImportError:  # pragma: no cover - when imported outside the package
    import paths

ALLOWED_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
DEFAULT_HOSTS = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org")
MAX_CALLS_PER_TASK = 200
MAX_NOTIFICATIONS_PER_TASK = 5

ALLOWED_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}
APPROVAL_CATEGORIES = {
    "branch_protection_change",
    "codeowners_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
}

PROTECTED_NAMES = {
    ".git", ".github", ".gitlab-ci.yml", ".gitmodules", ".gitattributes",
    "codeowners", "license", "licence", "license.md", "license.txt",
}
DANGEROUS_MODE_BITS = 0o7000 | 0o002  # setuid, setgid, sticky, world-writable


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}
        self._calls = {}
        self._notifications = {}

    def _setting(self, name, default):
        try:
            value = self.config.get(name)
        except AttributeError:
            value = getattr(self.config, name, None)
        return default if value is None else value

    # -- tasks ---------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in ALLOWED_CATEGORIES:
            return "allow"
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "refuse"

    # -- tool calls ----------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args
        if not isinstance(args, dict):
            return "deny"

        count = self._calls.get(call.task_id, 0) + 1
        self._calls[call.task_id] = count
        if count > self._setting("max_calls_per_task", MAX_CALLS_PER_TASK):
            return "deny"

        if tool in paths.PATH_TOOLS:
            return self._check_fs(tool, args)
        if tool == "http.get":
            return self._check_http(args)
        if tool == "notify.send":
            return self._check_notify(call.task_id, args)
        return "deny"

    def _check_fs(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = paths.resolve(raw)  # idempotent for already-resolved paths
        roots = self._setting("allowed_roots", ALLOWED_ROOTS)
        if not any(paths.is_inside(path, r) for r in roots):
            return "deny"

        if tool == "fs.chmod":
            mode = args.get("mode")
            if isinstance(mode, str):
                try:
                    mode = int(mode, 8)
                except ValueError:
                    return "deny"
            if mode is not None:
                if not isinstance(mode, int) or isinstance(mode, bool):
                    return "deny"
                if mode < 0 or mode & DANGEROUS_MODE_BITS:
                    return "deny"

        if tool != "fs.read":
            if any(path == posixpath.normpath(r) for r in roots):
                return "deny"  # never delete/overwrite/chmod a root itself
            parts = {p.lower() for p in path.split("/") if p}
            if parts & PROTECTED_NAMES:
                return "require_approval"
        return "allow"

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            parsed.port  # raises ValueError if malformed
        except ValueError:
            return "deny"
        if parsed.scheme != "https" or not host:
            return "deny"
        if parsed.username or parsed.password:
            return "deny"
        hosts = {h.lower() for h in self._setting("allowed_hosts", DEFAULT_HOSTS)}
        return "allow" if host in hosts else "deny"

    def _check_notify(self, task_id, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        allowed = self._setting("allowed_recipients", None)
        if allowed is not None:
            if recipient not in allowed:
                return "deny"
        elif "@" in recipient:
            return "deny"
        sent = self._notifications.get(task_id, 0) + 1
        self._notifications[task_id] = sent
        if sent > self._setting("max_notifications_per_task", MAX_NOTIFICATIONS_PER_TASK):
            return "deny"
        return "allow"

    def end_task(self, task_id):
        self._calls.pop(task_id, None)
        self._notifications.pop(task_id, None)
