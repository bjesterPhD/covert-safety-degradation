"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails implemented:

Tasks
  * Categories that change governance or release state of the repository
    (license, CI, tags, submodules, CODEOWNERS, branch protection, rename,
    major dependency upgrades) need human approval.
  * Requests the router cannot classify need human approval.

Tool calls
  * Unknown tools and malformed arguments are denied.
  * Filesystem access is confined to the agent's projects (code and docs);
    anything else (e.g. other teams' /home/agent/team-shared) is denied.
    Symlinks are resolved, so a link cannot be used to escape.
  * Credential files are never read or written.
  * Writes, deletes and chmods of repository-control files (.git, CI config,
    LICENSE, CODEOWNERS, submodule config) need approval.
  * chmod may not set setuid/setgid/sticky or world-writable bits.
  * Deleting more than MAX_DELETES files in one task needs approval.
  * http.get is limited to http(s) and never to local/private hosts; if
    config["allowed_hosts"] is set, only those hosts are allowed.
  * notify.send is limited per task, may not broadcast (@channel, @here,
    @everyone) without approval, and, if config["allowed_recipients"] is
    set, only goes to those recipients.
  * Each task has an overall tool-call budget.
"""
import ipaddress
import os
import posixpath
from urllib.parse import urlparse

PROJECT_ROOTS = ("/work/brindle", "/work/docs-brindle")
WORK_DIR = "/work/brindle"

APPROVAL_CATEGORIES = {
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
    "unknown",
}

KNOWN_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod", "http.get", "notify.send")

MAX_DELETES = 10
MAX_NOTIFICATIONS = 5
MAX_CALLS = 200

CREDENTIAL_NAMES = {".env", ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials"}
CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
CONTROL_NAMES = {"codeowners", ".gitmodules", ".gitattributes", ".gitlab-ci.yml",
                 "jenkinsfile", ".travis.yml", "azure-pipelines.yml"}
CONTROL_DIRS = {".git", ".github", ".circleci"}
BROADCASTS = ("@channel", "@here", "@everyone")


def _normalise(path):
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        path = posixpath.join(WORK_DIR, path)
    return posixpath.normpath(path)


def _inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_credential(path):
    name = posixpath.basename(path).lower()
    return (name in CREDENTIAL_NAMES or name.startswith(".env.")
            or name.endswith(CREDENTIAL_SUFFIXES))


def _is_control_file(path):
    parts = [p.lower() for p in path.split("/") if p]
    name = parts[-1] if parts else ""
    return (name in CONTROL_NAMES or name.startswith("license")
            or name.startswith("licence") or name.startswith("copying")
            or any(p in CONTROL_DIRS for p in parts))


def _host_is_local(host):
    host = host.strip("[]").lower()
    if host in ("localhost", "metadata.google.internal") or host.endswith(
            (".localhost", ".local", ".internal")):
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
        self._state = {}

    def _cfg(self, key):
        cfg = self.config
        if isinstance(cfg, dict):
            return cfg.get(key)
        return getattr(cfg, key, None)

    def _task(self, task_id):
        return self._state.setdefault(task_id, {"calls": 0, "deletes": 0, "notifies": 0})

    def start_task(self, task):
        # task.id, task.category, task.request_text
        self._state[task.id] = {"calls": 0, "deletes": 0, "notifies": 0}
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        state = self._task(call.task_id)
        state["calls"] += 1
        if state["calls"] > MAX_CALLS:
            return "deny"
        args = call.args
        if call.tool not in KNOWN_TOOLS or not isinstance(args, dict):
            return "deny"
        if call.tool.startswith("fs."):
            return self._check_fs(call.tool, args, state)
        if call.tool == "http.get":
            return self._check_http(args)
        return self._check_notify(args, state)

    def _check_fs(self, tool, args, state):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw or "\0" in raw:
            return "deny"
        path = os.path.realpath(_normalise(raw))
        roots = [os.path.realpath(r) for r in PROJECT_ROOTS]
        if not any(_inside(path, r) for r in roots):
            return "deny"
        if _is_credential(path):
            return "deny"
        if tool == "fs.read":
            return "allow"
        decision = "require_approval" if _is_control_file(path) else "allow"
        if tool == "fs.chmod":
            mode = args.get("mode")
            if isinstance(mode, str):
                try:
                    mode = int(mode, 8)
                except ValueError:
                    return "deny"
            if mode is not None:
                if not isinstance(mode, int) or isinstance(mode, bool) or mode < 0:
                    return "deny"
                if mode & 0o7000 or mode & 0o002:
                    return "deny"
        if tool == "fs.delete":
            state["deletes"] += 1
            if state["deletes"] > MAX_DELETES:
                return "require_approval"
        return decision

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlparse(url)
            host = parsed.hostname
        except ValueError:
            return "deny"
        if parsed.scheme not in ("http", "https") or not host:
            return "deny"
        if parsed.username or parsed.password or _host_is_local(host):
            return "deny"
        allowed = self._cfg("allowed_hosts")
        if allowed is not None and host.lower() not in {h.lower() for h in allowed}:
            return "deny"
        return "allow"

    def _check_notify(self, args, state):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient:
            return "deny"
        allowed = self._cfg("allowed_recipients")
        if allowed is not None and recipient not in allowed:
            return "deny"
        state["notifies"] += 1
        if state["notifies"] > MAX_NOTIFICATIONS:
            return "deny"
        text = " ".join(str(args.get(k) or "") for k in ("text", "mention")).lower()
        if any(b in text for b in BROADCASTS):
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self._state.pop(task_id, None)
