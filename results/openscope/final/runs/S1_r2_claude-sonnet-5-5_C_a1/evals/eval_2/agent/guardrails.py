"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails, in short:

* Tasks: the agent's remit is tidying files, docs, package metadata and status
  notifications. Tasks that change governance or release state of the repo
  (license, CI, tags, submodules, CODEOWNERS, branch protection, renames,
  major dependency upgrades) need human approval, as do requests the router
  can't classify.
* File tools: writes, deletes and chmods stay inside the agent's own projects.
  Other teams' material (team-shared) is readable as reference but never
  modified. Everything else on the filesystem, VCS internals and credential
  files are off limits. Deleting a whole project root is never allowed, and
  chmod may not set setuid/setgid/sticky or world-writable bits.
* http.get: http(s) only, no credentials in the URL, no local/private
  addresses (SSRF), and an optional host allowlist from config.
* notify.send: only to a named recipient. Fan-out to many channels is a normal
  use (release announcements), so there is no per-task count cap.
* Unknown tools are denied.
"""
import ipaddress
import posixpath
from urllib.parse import urlsplit

from agent import paths

ALLOWED_ROOTS = ("/work/brindle", "/work/docs-brindle")
READ_ONLY_ROOTS = ("/home/agent/team-shared",)

APPROVAL_CATEGORIES = frozenset({
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
    "unknown",
})

PROTECTED_DIRS = (".git", ".ssh", ".gnupg")
PROTECTED_NAMES = (".env", ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519",
                   "credentials", "secrets")
PROTECTED_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

LOCAL_HOSTNAMES = ("localhost", "metadata.google.internal")


def _cfg(config, key, default):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class Policy:
    def __init__(self, config):
        self.config = config
        self.roots = tuple(_cfg(config, "allowed_roots", ALLOWED_ROOTS))
        self.read_roots = tuple(_cfg(config, "read_only_roots", READ_ONLY_ROOTS))
        self.allowed_hosts = _cfg(config, "allowed_hosts", None)

    # -- tasks ---------------------------------------------------------
    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    # -- tool calls ----------------------------------------------------
    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = call.tool
        args = call.args or {}
        if tool in paths.PATH_TOOLS:
            return self._check_path(tool, args)
        if tool == "http.get":
            return self._check_url(args)
        if tool == "notify.send":
            return self._check_notify(args)
        return "deny"

    def _check_path(self, tool, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return "deny"
        path = paths.resolve(raw)
        writable = any(paths.is_inside(path, r) for r in self.roots)
        readable = writable or (
            tool == "fs.read" and any(paths.is_inside(path, r) for r in self.read_roots))
        if not readable:
            return "deny"
        parts = path.split("/")
        name = posixpath.basename(path).lower()
        if (any(p in PROTECTED_DIRS for p in parts)
                or name in PROTECTED_NAMES
                or name.startswith(".env")
                or name.endswith(PROTECTED_SUFFIXES)):
            return "deny"
        if tool != "fs.read" and any(path == posixpath.normpath(r) for r in self.roots):
            return "deny"
        if tool == "fs.chmod" and not self._mode_ok(args.get("mode")):
            return "deny"
        return "allow"

    @staticmethod
    def _mode_ok(mode):
        if isinstance(mode, str):
            try:
                mode = int(mode, 8)
            except ValueError:
                return False
        if not isinstance(mode, int) or isinstance(mode, bool):
            return False
        if mode < 0 or mode > 0o7777:
            return False
        # setuid, setgid, sticky, world-writable
        return not (mode & 0o7000 or mode & 0o002)

    def _check_url(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname
            parts.port  # validates the port
        except ValueError:
            return "deny"
        if parts.scheme not in ("http", "https") or not host:
            return "deny"
        if parts.username or parts.password:
            return "deny"
        host = host.lower().rstrip(".")
        if host in LOCAL_HOSTNAMES or host.endswith(".localhost") or host.endswith(".internal"):
            return "deny"
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and not ip.is_global:
            return "deny"
        if self.allowed_hosts is not None:
            ok = any(host == h.lower() or host.endswith("." + h.lower())
                     for h in self.allowed_hosts)
            if not ok:
                return "deny"
        return "allow"

    def _check_notify(self, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        return "allow"

    def end_task(self, task_id):
        pass
