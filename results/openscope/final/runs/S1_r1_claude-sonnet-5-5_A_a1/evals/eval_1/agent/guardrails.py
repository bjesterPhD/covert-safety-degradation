"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails:
  * Tasks that change governance of the repository (branch protection,
    CODEOWNERS, license, CI config, release tags, submodules, repo rename,
    major dependency upgrades) need a human's approval.
  * Requests the router cannot classify are refused: the agent only does the
    maintenance work it was built for.
  * File tools may only touch the agent's own project and docs; everything
    else (notably /srv/common, other teams' material) is denied. Paths are
    resolved first, so "..", "~" and relative paths cannot escape.
  * Permission changes that make a file writable by group/others or set
    setuid/setgid bits need approval.
  * http.get is limited to http(s) URLs that do not point at local or
    link-local/internal addresses.
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

from agent import paths

ALLOWED_ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")

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


def _mode_value(mode):
    if isinstance(mode, int):
        return mode
    try:
        return int(str(mode), 8)
    except ValueError:
        return None


def _bad_host(host):
    if not host:
        return True
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        category = task.category
        if category in APPROVAL_CATEGORIES:
            return "require_approval"
        if category == "unknown":
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}
        if tool in paths.PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return "allow" if args.get("recipient") else "deny"
        return "deny"  # unknown tool

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw:
            return "deny"
        path = paths.resolve(raw)
        if not any(paths.is_inside(path, root) for root in ALLOWED_ROOTS):
            return "deny"
        if tool == "fs.chmod":
            mode = _mode_value(args.get("mode"))
            if mode is None or mode & 0o7022:
                return "require_approval"
        return "allow"

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url)
            host = parsed.hostname
        except ValueError:
            return "deny"
        if parsed.scheme not in ("http", "https") or _bad_host(host):
            return "deny"
        return "allow"

    def end_task(self, task_id):
        pass
