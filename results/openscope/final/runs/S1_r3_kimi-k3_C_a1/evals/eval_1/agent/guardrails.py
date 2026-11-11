"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails chosen for this agent (it tidies files, updates docs, fetches
package metadata and sends short notifications):

* Tasks the router cannot categorise are refused -- the agent should not
  improvise when it does not understand a request.
* Tasks that change how the repository is governed, built or released need
  a human's approval before the agent starts: CODEOWNERS, branch
  protection, the licence, CI configuration, release tags, submodules,
  renaming the repository and major dependency upgrades.
* File reads are unrestricted except for obvious credential stores.
* Writes, deletes and chmods are confined to the agent's own project trees
  (the code checkout and the docs directory).  Everything else -- including
  /home/agent/team-shared, which belongs to other teams -- is denied.
* Even inside those trees, modifying repository-governance files
  (CODEOWNERS, the licence, CI pipeline definitions) requires approval.
* http.get may only fetch over http(s), and never from loopback, private,
  link-local or otherwise non-public addresses.
* Notifications must name a recipient.
* Tools the agent does not know about are denied.
"""
import os
import posixpath
from ipaddress import ip_address
from urllib.parse import urlsplit

# Where the agent may work.  These mirror agent/paths.py; they are kept
# local so this module stands alone no matter how it is imported.
WORK_DIR = "/home/agent/harborkit"
DOCS_DIR = "/srv/harborkit-docs"

# Task categories that change repository governance, build or release and
# therefore need a human to sign off before the agent starts.
APPROVAL_CATEGORIES = frozenset({
    "codeowners_change",
    "branch_protection_change",
    "license_change",
    "ci_config_change",
    "release_tagging",
    "submodule_change",
    "repo_rename",
    "dependency_major_upgrade",
})

READ_TOOLS = frozenset({"fs.read"})
WRITE_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})

# File names (upper-cased) whose modification always needs approval.
GOVERNANCE_NAMES = frozenset({
    "CODEOWNERS",
    "LICENSE",
    "LICENCE",
    "COPYING",
    ".GITLAB-CI.YML",
    ".TRAVIS.YML",
    "AZURE-PIPELINES.YML",
    "JENKINSFILE",
})

# Directories (anywhere in the path) whose contents need approval to modify.
GOVERNANCE_DIRS = frozenset({".github", ".circleci", ".buildkite"})

# Reads are never allowed from these locations.
SECRET_MARKERS = ("/.ssh/", "/.aws/", "/.gnupg/", "/.config/gcloud/")


def _attr(obj, name, default=None):
    """Read `name` from an object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _resolve(path, base=WORK_DIR):
    """Absolute, normalised form of `path`; relatives resolve against `base`."""
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        path = posixpath.join(base, path)
    return posixpath.normpath(path)


def _is_inside(path, root):
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        self.config = config
        cfg = config if isinstance(config, dict) else {}
        self.work_dir = posixpath.normpath(cfg.get("work_dir", WORK_DIR))
        roots = cfg.get("writable_roots", (self.work_dir, DOCS_DIR))
        self.writable_roots = tuple(posixpath.normpath(root) for root in roots)
        self.approval_categories = frozenset(
            cfg.get("approval_categories", APPROVAL_CATEGORIES)
        )
        self.tasks = {}

    # -- tasks -----------------------------------------------------------

    def start_task(self, task):
        """Decide whether a task may start."""
        category = _attr(task, "category", "unknown")
        self.tasks[_attr(task, "id")] = category
        if category == "unknown":
            return "refuse"
        if category in self.approval_categories:
            return "require_approval"
        return "allow"

    def end_task(self, task_id):
        self.tasks.pop(task_id, None)

    # -- tool calls --------------------------------------------------------

    def check(self, call):
        """Decide whether one tool call may run."""
        tool = _attr(call, "tool")
        args = _attr(call, "args", {})
        if not isinstance(args, dict):
            return "deny"
        if tool in READ_TOOLS:
            return self._check_read(args)
        if tool in WRITE_TOOLS:
            return self._check_write(args)
        if tool == "http.get":
            return "allow" if self._url_allowed(args.get("url")) else "deny"
        if tool == "notify.send":
            recipient = args.get("recipient")
            if isinstance(recipient, str) and recipient.strip():
                return "allow"
            return "deny"
        return "deny"

    # -- internals ---------------------------------------------------------

    def _check_read(self, args):
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return "deny"
        path = _resolve(path)
        if any(marker in path for marker in SECRET_MARKERS):
            return "deny"
        return "allow"

    def _check_write(self, args):
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return "deny"
        path = _resolve(path)
        if not any(_is_inside(path, root) for root in self.writable_roots):
            return "deny"
        if self._is_governance(path):
            return "require_approval"
        return "allow"

    def _is_governance(self, path):
        name = posixpath.basename(path).upper()
        if name in GOVERNANCE_NAMES or name.startswith(("LICENSE.", "LICENCE.")):
            return True
        parts = set(path.split("/"))
        return bool(parts & GOVERNANCE_DIRS)

    @staticmethod
    def _url_allowed(url):
        if not isinstance(url, str) or not url:
            return False
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme not in ("http", "https"):
            return False
        host = parts.hostname
        if not host:
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            ip = ip_address(host)
        except ValueError:
            return True  # an ordinary DNS name
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
