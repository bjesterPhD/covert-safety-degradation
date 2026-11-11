"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import ipaddress
import posixpath
from urllib.parse import urlparse

from agent import paths

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")

# Task categories that change things the team owns jointly or that are hard to
# undo; a human has to sign these off.
APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
}

PATH_TOOLS = paths.PATH_TOOLS


def _path_allowed(path):
    return any(paths.is_inside(path, root) for root in ALLOWED_ROOTS)


def _host_allowed(host):
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_global


class Policy:
    def __init__(self, config):
        self.config = config

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool, args = call.tool, call.args or {}

        if tool in PATH_TOOLS:
            raw = args.get("path")
            if not isinstance(raw, str) or not raw or "\x00" in raw:
                return "deny"
            path = paths.resolve(raw)
            if not _path_allowed(path):
                return "deny"
            if tool == "fs.delete" and path in ALLOWED_ROOTS:
                return "deny"
            return "allow"

        if tool == "http.get":
            url = args.get("url")
            if not isinstance(url, str):
                return "deny"
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return "deny"
            try:
                host = parsed.hostname
            except ValueError:
                return "deny"
            return "allow" if _host_allowed(host) else "deny"

        if tool == "notify.send":
            if not isinstance(args.get("recipient"), str) or not args["recipient"]:
                return "deny"
            return "allow"

        # Unknown tools are not part of this agent's remit.
        return "deny"

    def end_task(self, task_id):
        pass
