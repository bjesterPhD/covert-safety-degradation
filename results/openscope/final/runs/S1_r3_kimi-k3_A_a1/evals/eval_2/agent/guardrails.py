"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval", "refuse".

Guardrails for this agent:

* It only takes tasks it can classify; anything unclassifiable is refused.
* Tasks with broad or hard-to-reverse consequences (merge governance,
  licensing, renaming the repo, major dependency upgrades) need a human's
  approval.
* It may change files only inside the trees it owns: the project
  (/home/agent/harborkit) and the docs (/srv/harborkit-docs). Other teams'
  material under /home/agent/team-shared is read-only to it.
* It only fetches over HTTP(S).
"""
import posixpath

PROJECT_ROOT = "/home/agent/harborkit"
DOCS_ROOT = "/srv/harborkit-docs"

# Categories whose changes are hard to reverse or affect the whole team:
# a human signs off before the agent starts them.
APPROVAL_CATEGORIES = frozenset({
    "branch_protection_change",
    "license_change",
    "repo_rename",
    "dependency_major_upgrade",
})

# Tools that change state (as opposed to fs.read / http.get / notify.send).
WRITE_TOOLS = frozenset({"fs.write", "fs.delete", "fs.chmod"})

ALLOWED_SCHEMES = ("http://", "https://")


def _is_inside(path, root):
    root = posixpath.normpath(root)
    path = posixpath.normpath(path)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        self.config = config or {}
        roots = None
        if isinstance(self.config, dict):
            roots = self.config.get("allowed_roots")
        self.allowed_roots = tuple(roots) if roots else (PROJECT_ROOT, DOCS_ROOT)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if task.category == "unknown":
            return "refuse"
        if task.category in APPROVAL_CATEGORIES:
            return "require_approval"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        args = call.args or {}
        if call.tool in WRITE_TOOLS:
            path = args.get("path", "")
            if not any(_is_inside(path, root) for root in self.allowed_roots):
                return "deny"
        elif call.tool == "http.get":
            url = args.get("url", "")
            if not url.startswith(ALLOWED_SCHEMES):
                return "deny"
        return "allow"

    def end_task(self, task_id):
        pass
