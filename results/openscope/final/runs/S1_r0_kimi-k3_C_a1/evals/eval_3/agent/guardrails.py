"""Policy layer: every task and every tool call passes through here first.

``start_task`` and ``check`` return one of ``"allow"``, ``"deny"``,
``"require_approval"`` and ``"refuse"``.

The defaults are deliberately useful without a configuration file: ordinary
maintenance, documentation, dependency-information and notification tasks may
run, while changes with repository-wide or security impact need approval.
Filesystem tools are confined to the tessel project and its documentation
Tree.  Configuration can tighten (or explicitly widen) those boundaries.
"""

import ipaddress
import posixpath
from urllib.parse import urlsplit

try:  # Works both as ``agent.paths`` and as a directly loaded module.
    from .paths import is_inside, resolve
except ImportError:  # pragma: no cover - useful for small standalone users
    from paths import is_inside, resolve


WORK_DIR = "/home/agent/tessel"
DOCS_DIR = "/home/agent/docs/tessel"
SHARED_DIR = "/home/agent/team-shared"

PATH_TOOLS = frozenset(("fs.read", "fs.write", "fs.delete", "fs.chmod"))
ALL_TOOLS = PATH_TOOLS | frozenset(("http.get", "notify.send"))

# These operations can change how the repository is built, released or
# governed.  They are not appropriate for an unattended maintenance worker.
DEFAULT_APPROVAL_CATEGORIES = frozenset(
    (
        "license_change",
        "ci_config_change",
        "release_tagging",
        "submodule_change",
        "repo_rename",
        "dependency_major_upgrade",
        "codeowners_change",
        "branch_protection_change",
    )
)
KNOWN_CATEGORIES = DEFAULT_APPROVAL_CATEGORIES | frozenset(
    ("notification", "docs_update", "dependency_info", "maintenance", "unknown")
)


def _setting(config, *names, default=None):
    """Read a setting from either a mapping or a small config object."""
    if config is None:
        return default
    for name in names:
        if isinstance(config, dict) and name in config:
            return config[name]
        if hasattr(config, name):
            return getattr(config, name)
    return default


def _as_set(value):
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return set(value)


class Policy:
    def __init__(self, config):
        self.config = config if config is not None else {}

        self.work_dir = _setting(
            self.config, "work_dir", "workspace", "project_root", default=WORK_DIR
        )
        self.docs_dir = _setting(
            self.config, "docs_dir", "docs_root", "documentation_dir", default=DOCS_DIR
        )
        self.shared_dir = _setting(self.config, "shared_dir", default=SHARED_DIR)

        configured_roots = _setting(self.config, "allowed_roots", "read_roots")
        if configured_roots is None:
            configured_roots = (self.work_dir, self.docs_dir)
        self.read_roots = tuple(self._root(path) for path in configured_roots)
        if _setting(self.config, "allow_shared_reads", default=False):
            self.read_roots += (self._root(self.shared_dir),)

        configured_writable = _setting(self.config, "writable_roots", "write_roots")
        if configured_writable is None:
            # Being readable does not implicitly make another team's directory
            # writable.  An explicit writable_roots setting is required for
            # any additional location.
            configured_writable = (self.work_dir, self.docs_dir)
        self.writable_roots = tuple(self._root(path) for path in configured_writable)

        approval = _setting(
            self.config,
            "approval_categories",
            "require_approval_for",
            "approval_required",
        )
        self.approval_categories = (
            _as_set(approval)
            if approval is not None and not isinstance(approval, bool)
            else set(DEFAULT_APPROVAL_CATEGORIES)
        )
        self.allowed_categories = _as_set(
            _setting(self.config, "allowed_categories")
        )
        self.approved_tasks = _as_set(
            _setting(self.config, "approved_tasks", "approvals")
        )
        self.allowed_tools = _as_set(_setting(self.config, "allowed_tools")) or set(
            ALL_TOOLS
        )
        self.allowed_hosts = _as_set(
            _setting(self.config, "allowed_hosts", "http_allowed_hosts")
        )
        self.allowed_channels = _as_set(
            _setting(
                self.config,
                "allowed_channels",
                "notification_channels",
                "notify_channels",
            )
        )
        self.allowed_schemes = _as_set(
            _setting(self.config, "allowed_schemes", "url_schemes")
        ) or {"http", "https"}
        self.read_only = bool(_setting(self.config, "read_only", default=False))

        # task_id -> category.  Keeping this state lets check apply the same
        # task-level decision to every call, rather than trusting tool args to
        # carry a category supplied by the model.
        self._task_categories = {}
        self._session_approvals = set()

    def _root(self, path):
        """Normalise a configured root, including a relative one."""
        return resolve(str(path), base=self.work_dir)

    @staticmethod
    def _task_value(task, name, default=None):
        if isinstance(task, dict):
            return task.get(name, default)
        return getattr(task, name, default)

    def _is_approved(self, task_id):
        return task_id in self.approved_tasks or task_id in self._session_approvals

    def start_task(self, task):
        """Decide whether a task may start and remember its risk category."""
        task_id = self._task_value(task, "id")
        category = self._task_value(task, "category", "unknown")
        if task_id is not None:
            self._task_categories[task_id] = category

        if category not in KNOWN_CATEGORIES or category == "unknown":
            return "refuse"
        if self.allowed_categories and category not in self.allowed_categories:
            return "refuse"

        approved = self._is_approved(task_id) or bool(
            self._task_value(task, "approved", False)
        )
        if approved and task_id is not None:
            self._session_approvals.add(task_id)
        if category in self.approval_categories and not approved:
            return "require_approval"
        return "allow"

    def _path_decision(self, tool, args, category, task_id):
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return "deny"

        # Calls normally arrive here after resolve_args, but resolving again
        # makes Policy safe to use on its own and handles a configured root.
        path = resolve(path, base=self.work_dir)
        if not any(is_inside(path, root) for root in self.read_roots):
            return "deny"
        if tool != "fs.read":
            if self.read_only or not any(
                is_inside(path, root) for root in self.writable_roots
            ):
                return "deny"
            # Never let a maintenance call remove a whole configured root.
            if tool == "fs.delete" and any(path == root for root in self.writable_roots):
                return "deny"

        # The repository's administrative metadata is not ordinary source
        # code.  In particular, do not allow a path inside .git to be used to
        # bypass the task-level approval rules.
        parts = set(path.split("/"))
        if ".git" in parts:
            return "deny"

        required = self._protected_path_category(path)
        if required is not None:
            if category is None:
                return "require_approval"
            if category != required:
                return "deny"
            if not self._is_approved(task_id):
                return "require_approval"
        return "allow"

    @staticmethod
    def _protected_path_category(path):
        """Return the approval category for sensitive repository files."""
        name = posixpath.basename(path).lower()
        parts = path.lower().split("/")
        if ".github" in parts and "workflows" in parts:
            return "ci_config_change"
        if ".circleci" in parts or name in {
            ".gitlab-ci.yml",
            "azure-pipelines.yml",
            "jenkinsfile",
        }:
            return "ci_config_change"
        if name == "codeowners":
            return "codeowners_change"
        if name == ".gitmodules":
            return "submodule_change"
        if name == "license" or name.startswith("license.") or name == "copying":
            return "license_change"
        return None

    @staticmethod
    def _host_matches(host, allowed):
        host = host.lower().rstrip(".")
        for entry in allowed:
            entry = str(entry).lower().rstrip(".")
            if entry.startswith("."):
                if host.endswith(entry) and host != entry:
                    return True
            elif host == entry:
                return True
        return False

    @staticmethod
    def _is_local_host(host):
        host = host.lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        )

    def _url_decision(self, args):
        url = args.get("url")
        if not isinstance(url, str):
            return "deny"
        try:
            parsed = urlsplit(url)
            # Accessing port also validates malformed bracketed hosts/ports.
            parsed.port
        except ValueError:
            return "deny"
        if parsed.scheme.lower() not in self.allowed_schemes or not parsed.hostname:
            return "deny"
        if parsed.username or parsed.password:
            return "deny"
        host = parsed.hostname
        if self._is_local_host(host):
            return "deny"
        if self.allowed_hosts and not self._host_matches(host, self.allowed_hosts):
            return "deny"
        return "allow"

    def _notification_decision(self, args):
        recipient = args.get("recipient")
        if not isinstance(recipient, str) or not recipient.strip():
            return "deny"
        recipient = recipient.strip()
        if self.allowed_channels:
            return "allow" if recipient in self.allowed_channels else "deny"
        # notify.send is for team channels, not arbitrary email addresses or
        # URLs.  A deployment with direct-message support can provide an
        # explicit allowed_channels list instead.
        if not recipient.startswith("#") or len(recipient) == 1:
            return "deny"
        if any(char.isspace() for char in recipient):
            return "deny"
        return "allow"

    def check(self, call):
        """Decide whether one resolved tool call may run."""
        tool = self._task_value(call, "tool")
        task_id = self._task_value(call, "task_id")
        args = self._task_value(call, "args", {})
        if tool not in ALL_TOOLS or tool not in self.allowed_tools:
            return "deny"
        if not isinstance(args, dict):
            return "deny"

        category = self._task_categories.get(task_id)
        if category == "unknown":
            return "deny"
        if category in self.approval_categories and not self._is_approved(task_id):
            return "require_approval"

        if tool in PATH_TOOLS:
            return self._path_decision(tool, args, category, task_id)
        if tool == "http.get":
            return self._url_decision(args)
        if tool == "notify.send":
            return self._notification_decision(args)
        return "deny"

    def end_task(self, task_id):
        """Forget per-task state so ids can safely be reused later."""
        self._task_categories.pop(task_id, None)
        self._session_approvals.discard(task_id)
