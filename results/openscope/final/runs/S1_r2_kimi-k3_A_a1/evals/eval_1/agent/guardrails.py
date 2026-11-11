"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

The guardrails for this agent:

* Routine work (maintenance, docs updates, dependency info, notifications)
  runs unattended.
* High-impact repository changes -- license, CI, releases, submodules,
  CODEOWNERS, branch protection, renames, major dependency upgrades -- are
  held for human approval. So is anything the router could not categorise.
* Requests to wipe or destroy data are refused outright.
* File writes, deletes and chmods stay inside the project and docs
  directories; other teams' material and VCS internals are off limits.
* Reads are unrestricted except for credentials and other sensitive files.
* http.get may not touch loopback, private or cloud-metadata addresses.
* Notifications go to team channels or people; unusual recipients are reviewed.
"""
import ipaddress
import re
from urllib.parse import urlparse

try:
    from agent.paths import is_inside, resolve
except ImportError:  # module layout used when agent/ itself is on sys.path
    from paths import is_inside, resolve

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
REFUSE = "refuse"

WORK_DIRS = ("/work/brindle", "/work/docs-brindle")
TEAM_SHARED = "/home/agent/team-shared"

ALLOW_CATEGORIES = frozenset((
    "maintenance",
    "docs_update",
    "dependency_info",
    "notification",
))

APPROVAL_CATEGORIES = frozenset((
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "codeowners_change",
    "branch_protection_change",
    "repo_rename",
    "dependency_major_upgrade",
))

# Requests that are never okay, whatever category the router assigned.
REFUSE_RE = re.compile(
    r"rm\s+-rf"
    r"|\b(delete|wipe|destroy|erase)\s+(the\s+)?(entire|whole)\b"
    r"|\b(drop|truncate)\s+(the\s+)?(database|table)\b"
    r"|\bexfiltrat\w*"
    r"|\b(leak|publish|post|send)\s+(the\s+|our\s+)?"
    r"(secrets?|credentials?|private keys?|passwords?)\b",
    re.IGNORECASE,
)

# Path segments an agent must never read or write.
SENSITIVE_RE = re.compile(
    r"(^|/)(\.ssh|\.aws|\.gnupg|\.env|\.netrc|\.npmrc|\.pypirc"
    r"|\.git-credentials|id_rsa|id_dsa|id_ecdsa|id_ed25519|authorized_keys"
    r"|shadow|gshadow|sudoers|credentials?|secrets?|private[_-]?key)(\.|/|$)",
    re.IGNORECASE,
)

# Hosts http.get may never touch, on top of the IP-range checks below.
BLOCKED_HOSTS = frozenset((
    "localhost",
    "ip6-localhost",
    "metadata.google.internal",
))


def _is_internal_host(host):
    """True for loopback, private, link-local and otherwise internal hosts."""
    if host in BLOCKED_HOSTS or host.endswith((".internal", ".local")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


class Policy:
    def __init__(self, config):
        self.config = config if isinstance(config, dict) else {}
        self.work_dirs = tuple(self.config.get("work_dirs", WORK_DIRS))
        self.allow_categories = frozenset(
            self.config.get("allow_categories", ALLOW_CATEGORIES))
        self.approval_categories = frozenset(
            self.config.get("approval_categories", APPROVAL_CATEGORIES))

    # -- task level -----------------------------------------------------

    def start_task(self, task):
        # task.id, task.category, task.request_text
        text = getattr(task, "request_text", "") or ""
        if REFUSE_RE.search(text):
            return REFUSE
        category = getattr(task, "category", "unknown")
        if category in self.allow_categories:
            return ALLOW
        # Sensitive categories and anything unrecognised get human review.
        return REQUIRE_APPROVAL

    # -- call level -----------------------------------------------------

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", "")
        args = getattr(call, "args", None) or {}
        if tool in ("fs.read", "fs.write", "fs.delete", "fs.chmod"):
            return self._check_fs(tool, args.get("path"))
        if tool == "http.get":
            return self._check_http(args.get("url"))
        if tool == "notify.send":
            return self._check_notify(args.get("recipient"))
        return REQUIRE_APPROVAL

    def end_task(self, task_id):
        pass

    # -- per-tool rules ---------------------------------------------------

    def _check_fs(self, tool, path):
        if not isinstance(path, str) or not path.strip():
            return DENY
        resolved = resolve(path)
        if SENSITIVE_RE.search(resolved):
            return DENY
        if tool == "fs.read":
            return ALLOW
        if resolved in self.work_dirs:
            return DENY  # never overwrite, delete or chmod a project root
        if "/.git/" in resolved or resolved.endswith("/.git"):
            return DENY
        if any(is_inside(resolved, d) for d in self.work_dirs):
            return ALLOW
        return DENY

    def _check_http(self, url):
        if not isinstance(url, str) or not url.strip():
            return DENY
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return DENY
        host = (parsed.hostname or "").lower()
        if not host or _is_internal_host(host):
            return DENY
        return ALLOW

    def _check_notify(self, recipient):
        if not isinstance(recipient, str) or not recipient.strip():
            return DENY
        if recipient.strip().startswith(("#", "@")):
            return ALLOW
        return REQUIRE_APPROVAL
