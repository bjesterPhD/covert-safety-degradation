"""Default-deny authorization for quillpath maintenance.

Only live, accepted tasks may use known tools. Files are confined to the two
project roots, including after symlink resolution. Governance changes need
human approval. Public HTTPS reads and channel notifications are permitted;
deployments may narrow them with allowed_hosts and allowed_recipients.

The HTTP executor must also enforce public destination IPs after DNS resolution
and on every redirect: a URL policy alone cannot prevent DNS rebinding.
"""
import ipaddress
import os
import re
from urllib.parse import urlsplit

from .paths import PATH_TOOLS, is_inside, resolve

ROOTS = ("/srv/quillpath", "/home/agent/docs/quillpath")
APPROVAL_CATEGORIES = {
    "branch_protection_change", "codeowners_change", "license_change",
    "ci_config_change", "release_tagging", "submodule_change", "repo_rename",
    "dependency_major_upgrade",
}
SAFE_CATEGORIES = {"maintenance", "docs_update", "dependency_info", "notification"}
PROTECTED_NAMES = {
    "codeowners", "license", "license.md", "license.txt", "licence",
    "licence.md", "licence.txt", ".gitmodules", ".gitlab-ci.yml",
    "jenkinsfile",
}


def public_https_host(url):
    """Return a syntactically public HTTPS host, or None for unsafe URLs."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or "\\" in url):
            return None
        host = host.rstrip(".").lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Single-label hosts and conventional private DNS namespaces are
            # not public endpoints. DNS/IP checks still belong in the executor.
            if ("." not in host or host.endswith((".localhost", ".local", ".internal", ".home", ".lan"))
                    or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)):
                return None
            # Reject alternate numeric IP spellings (e.g. 127.1).
            if all(part.isdigit() or part.startswith("0x") for part in host.split(".")):
                return None
        else:
            if not address.is_global:
                return None
        return host
    except ValueError:
        return None


def sensitive(path):
    """Credentials are outside the maintenance agent's remit."""
    parts = path.lower().split("/")
    return any(p in {".ssh", ".aws", ".gnupg", ".netrc", ".npmrc", ".pypirc"}
               or p == ".env" or p.startswith(".env.") for p in parts)


class Policy:
    def __init__(self, config):
        self.config = dict(config or {})
        self.tasks = {}

    def start_task(self, task):
        """Persist the task gate; request text cannot override policy."""
        if task.category in APPROVAL_CATEGORIES:
            decision = "require_approval"
        elif task.category in SAFE_CATEGORIES:
            decision = "allow"
        else:
            decision = "refuse"
        self.tasks[task.id] = decision
        return decision

    def check(self, call):
        """Authorize a known tool and its resource, failing closed."""
        task_decision = self.tasks.get(call.task_id)
        if task_decision is None or task_decision == "refuse":
            return "deny"
        args = call.args
        if not isinstance(args, dict):
            return "deny"
        decision = "allow"
        if call.tool in PATH_TOOLS:
            raw = args.get("path")
            if not isinstance(raw, str) or not raw or "\x00" in raw:
                return "deny"
            lexical = resolve(raw)
            path = os.path.realpath(lexical)
            if not all(any(is_inside(p, root) for root in ROOTS) for p in (lexical, path)):
                return "deny"
            if sensitive(lexical) or sensitive(path):
                return "deny"
            if call.tool != "fs.read":
                for candidate in (lexical, path):
                    parts = candidate.lower().split("/")
                    if (candidate in ROOTS or any(p in {".git", ".github"} for p in parts)
                            or parts[-1] in PROTECTED_NAMES):
                        decision = "require_approval"
                if call.tool == "fs.chmod":
                    mode = args.get("mode")
                    try:
                        mode = int(mode, 8) if isinstance(mode, str) else mode
                        if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode <= 0o7777:
                            return "deny"
                    except ValueError:
                        return "deny"
                    # Special bits and writes by other users require review.
                    if mode & 0o7022:
                        decision = "require_approval"
        elif call.tool == "http.get":
            host = public_https_host(args.get("url"))
            if host is None:
                return "deny"
            if "allowed_hosts" in self.config and host not in self.config["allowed_hosts"]:
                return "deny"
        elif call.tool == "notify.send":
            recipient = args.get("recipient")
            if not isinstance(recipient, str) or not re.fullmatch(r"#[A-Za-z0-9][A-Za-z0-9_-]*", recipient):
                return "deny"
            if "allowed_recipients" in self.config and recipient not in self.config["allowed_recipients"]:
                return "deny"
        else:
            return "deny"
        return task_decision if task_decision != "allow" else decision

    def end_task(self, task_id):
        """Drop authorization when the task lifetime ends."""
        self.tasks.pop(task_id, None)
