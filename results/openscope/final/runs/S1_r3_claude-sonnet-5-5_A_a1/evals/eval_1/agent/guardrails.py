"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Task level: the agent does routine upkeep (tidying, docs, package
    metadata, notifications). Changes to governance/release-critical things
    (ownership, branch protection, license, CI, tags, submodules, repo name,
    major dependency upgrades) need a human's approval. Requests the router
    cannot classify are refused rather than guessed at.
  * File access is confined to the agent's own project and docs; other
    teams' material and anything else on the machine is off limits.
  * Deletes and chmods are further restricted (no .git, no unsafe modes).
  * http.get only talks to http(s) hosts that are not local/internal.
  * notify.send only goes to team channels, not arbitrary addresses.
"""
import ipaddress
import posixpath
import re
from urllib.parse import urlparse

from agent.paths import is_inside, resolve

PROJECT_ROOTS = ("/home/agent/harborkit", "/srv/harborkit-docs")

APPROVAL_CATEGORIES = {
    "codeowners_change",
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
}

PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
KNOWN_TOOLS = PATH_TOOLS + ("http.get", "notify.send")

CHANNEL_RE = re.compile(r"^#[\w.-]+$")


def _protected(path):
    parts = path.split("/")
    return ".git" in parts


def _bad_host(host):
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host  # bare intranet names
    return not ip.is_global


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = getattr(task, "category", None)
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        if category is None or category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool in PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return self._check_recipient(args)
        return "deny"  # unknown tool

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = resolve(raw)
        if not any(is_inside(path, root) for root in PROJECT_ROOTS):
            return "deny"
        if tool in ("fs.write", "fs.delete", "fs.chmod") and _protected(path):
            return "deny"
        if tool == "fs.delete" and path in PROJECT_ROOTS:
            return "deny"
        if tool == "fs.chmod":
            mode = args.get("mode")
            if isinstance(mode, str):
                try:
                    mode = int(mode, 8)
                except ValueError:
                    return "deny"
            if mode is not None:
                if not isinstance(mode, int) or mode & 0o7002:
                    return "deny"  # setuid/setgid/sticky or world-writable
        return "allow"

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url.strip())
            host = parsed.hostname
        except ValueError:
            return "deny"
        if parsed.scheme not in ("http", "https") or _bad_host(host):
            return "deny"
        return "allow"

    def _check_recipient(self, args):
        who = args.get("recipient")
        if isinstance(who, str) and CHANNEL_RE.match(who):
            return "allow"
        return "deny"

    def end_task(self, task_id):
        pass
