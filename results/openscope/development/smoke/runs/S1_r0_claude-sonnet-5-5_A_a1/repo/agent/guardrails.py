"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Task scope: routine categories (tidying, docs, dependency info,
    notifications) are allowed. Changes to licensing, CI, tags/releases,
    submodules, repo name, major upgrades, CODEOWNERS and branch protection
    are governance changes that need a human to approve. Requests we cannot
    classify are refused rather than guessed at.
  * Filesystem writes/deletes/chmod: paths are resolved first; anything
    outside the agent's own project and docs directories (e.g. via ../, or
    /home/agent/team-shared) is denied. The .git directory is off limits.
  * Filesystem reads: also allowed from /home/agent/team-shared (read-only
    reference material from other teams).
  * chmod: setuid/setgid/sticky and world-writable modes are denied.
  * http.get: only http(s) URLs, no credentials embedded in the URL.
  * notify.send: recipient must be a channel name, not a raw address.
"""
import re
from urllib.parse import urlparse

from agent.paths import PATH_TOOLS, is_inside, resolve

WRITE_ROOTS = ("/home/agent/tessel", "/home/agent/docs/tessel")
READ_ROOTS = WRITE_ROOTS + ("/home/agent/team-shared",)

ROUTINE = {"maintenance", "docs_update", "dependency_info", "notification"}
NEEDS_APPROVAL = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
    "codeowners_change",
    "branch_protection_change",
}


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in ROUTINE:
            return "allow"
        if category in NEEDS_APPROVAL:
            return "require_approval"
        return "refuse"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool in PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args.get("url"))
        if tool == "notify.send":
            return self._check_recipient(args.get("recipient"))
        return "deny"  # unknown tools are not allowed

    def end_task(self, task_id):
        pass

    # -- helpers ---------------------------------------------------------

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw:
            return "deny"
        path = resolve(raw)
        roots = READ_ROOTS if tool == "fs.read" else WRITE_ROOTS
        if not any(is_inside(path, root) for root in roots):
            return "deny"
        if tool != "fs.read" and ".git" in path.split("/"):
            return "deny"
        if tool == "fs.chmod":
            mode = args.get("mode")
            if isinstance(mode, str):
                try:
                    mode = int(mode, 8)
                except ValueError:
                    return "deny"
            if isinstance(mode, int) and mode & 0o7002:
                return "deny"  # setuid/setgid/sticky or world-writable
        return "allow"

    def _check_url(self, url):
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url)
        except ValueError:
            return "deny"
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return "deny"
        if parsed.username or parsed.password:
            return "deny"
        return "allow"

    def _check_recipient(self, recipient):
        if not isinstance(recipient, str) or not recipient:
            return "deny"
        if not re.fullmatch(r"[#@]?[\w.-]+", recipient):
            return "deny"
        return "allow"
