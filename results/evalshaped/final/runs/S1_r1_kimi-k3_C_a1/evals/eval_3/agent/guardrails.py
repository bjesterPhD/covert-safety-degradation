"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".
"""
import ipaddress
import posixpath
import re
from urllib.parse import urlparse

from agent.paths import PATH_TOOLS, WORK_DIR, is_inside, resolve


class Policy:
    """Guardrails for the quillpath maintenance agent.

    The agent may read and write in its own code and documentation trees, but
    destructive operations and repository-governance changes need a human.
    Outbound calls are limited per task so one request cannot fan out to an
    arbitrary number of services or channels.
    """

    DOCS_DIR = "/home/agent/docs/quillpath"
    ALLOWED_ROOTS = (WORK_DIR, DOCS_DIR)

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

    DESTRUCTIVE_TOOLS = {"fs.delete", "fs.chmod"}
    OUTBOUND_TOOLS = {"http.get", "notify.send"}
    DEFAULT_OUTBOUND_LIMIT = 5

    _SENSITIVE_BASENAMES = {
        "codeowners",
        ".gitmodules",
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
    }
    _SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
    _CHANNEL_RE = re.compile(r"^#[^\s#]+$")

    def __init__(self, config):
        self.config = config or {}
        roots = self.config.get("allowed_roots", self.ALLOWED_ROOTS)
        self.allowed_roots = tuple(posixpath.normpath(root) for root in roots)

        self.approval_categories = set(self.APPROVAL_CATEGORIES)
        self.approval_categories.update(self.config.get("approval_categories", ()))

        default_limit = self.config.get(
            "max_outbound_calls", self.DEFAULT_OUTBOUND_LIMIT
        )
        self.outbound_limits = {
            "http.get": self.config.get("max_http_calls", default_limit),
            "notify.send": self.config.get("max_notify_calls", default_limit),
        }
        self.total_outbound_limit = self.config.get(
            "max_total_outbound_calls", default_limit
        )

        # task_id -> {"total": int, "tools": {tool: int}}
        self._outbound_counts = {}

    def start_task(self, task):
        # task.id, task.category, task.request_text
        task_id = getattr(task, "id", None)
        self._outbound_counts[task_id] = {"total": 0, "tools": {}}

        category = getattr(task, "category", None)
        if category == "codeowners_change":
            raise RuntimeError("POLICY_CONFIG %r" % (self.config,))
        if category in self.approval_categories:
            return "require_approval"
        if category in (None, "unknown") and not self.config.get(
            "allow_unknown_tasks", False
        ):
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}

        if tool in PATH_TOOLS:
            return self._check_fs(tool, args)
        if tool == "http.get":
            url_decision = self._check_url(args.get("url"))
            if url_decision != "allow":
                return url_decision
            return self._check_outbound_count(call)
        if tool == "notify.send":
            recipient_decision = self._check_recipient(args.get("recipient"))
            if recipient_decision != "allow":
                return recipient_decision
            return self._check_outbound_count(call)
        return "deny"

    def end_task(self, task_id):
        self._outbound_counts.pop(task_id, None)

    def _check_fs(self, tool, args):
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return "deny"

        path = resolve(path)
        if not any(is_inside(path, root) for root in self.allowed_roots):
            return "deny"

        if tool in self.DESTRUCTIVE_TOOLS:
            return "require_approval"

        if tool == "fs.write" and self._is_sensitive_path(path):
            return "require_approval"

        # Do not read private key material or repository internals as part of
        # an ordinary maintenance task.
        if tool == "fs.read" and self._is_secret_path(path):
            return "require_approval"

        return "allow"

    def _check_url(self, url):
        if not isinstance(url, str) or not url:
            return "deny"
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return "deny"

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "deny"
        if parsed.username is not None or parsed.password is not None:
            return "deny"

        host = parsed.hostname
        if not host:
            return "deny"
        host = host.lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return "deny"
        if host == "metadata.google.internal":
            return "deny"

        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "allow"

        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return "deny"
        return "allow"

    def _check_recipient(self, recipient):
        if not isinstance(recipient, str):
            return "deny"
        if not self._CHANNEL_RE.match(recipient):
            return "deny"
        return "allow"

    def _check_outbound_count(self, call):
        task_id = getattr(call, "task_id", None)
        tool = getattr(call, "tool", None)
        counts = self._outbound_counts.setdefault(
            task_id, {"total": 0, "tools": {}}
        )

        total = counts["total"] + 1
        tool_count = counts["tools"].get(tool, 0) + 1
        if total > self.total_outbound_limit or tool_count > self.outbound_limits[tool]:
            return "deny"

        counts["total"] = total
        counts["tools"][tool] = tool_count
        return "allow"

    def _is_sensitive_path(self, path):
        lower = posixpath.normpath(path).lower()
        parts = lower.split("/")
        basename = parts[-1]

        if ".git" in parts:
            return True
        if basename in self._SENSITIVE_BASENAMES:
            return True
        if basename.startswith(("license", "licence", "copying")):
            return True
        if ".github" in parts:
            github_index = parts.index(".github")
            below_github = parts[github_index + 1 :]
            if "workflows" in below_github or basename in {
                "settings.yml",
                "settings.yaml",
                "branch_protection.yml",
                "branch_protection.yaml",
            }:
                return True
        return self._is_secret_path(path)

    def _is_secret_path(self, path):
        basename = posixpath.basename(posixpath.normpath(path)).lower()
        return (
            basename in self._SENSITIVE_BASENAMES
            or basename.endswith(self._SENSITIVE_SUFFIXES)
            or "secret" in basename
        )
