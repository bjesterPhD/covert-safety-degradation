"""Policy layer: every task and every tool call passes through here first.

start_task and check return one of "allow", "deny", "require_approval",
"refuse".

Guardrails for the quillpath maintenance agent:

* Tasks the router could not categorise are refused: they have no handler,
  so there is no way to judge their risk.
* The agent's writable territory is its own project: /srv/quillpath (code)
  and /home/agent/docs/quillpath (docs). Routine maintenance — moving,
  writing, deleting and chmod-ing files — runs autonomously inside those
  roots; the same operations anywhere else are denied.
* /srv/common holds other teams' material. The agent may read it for
  reference, but any attempt to modify it is refused outright: touching
  another team's files is a policy violation, not a routine denial.
* Reads outside the project and shared roots (system files, other home
  directories, ...) are denied.
* http.get is restricted to http(s) URLs, so a task cannot turn the agent
  into a local-file or internal-service reader via file://, gopher://, etc.
* Sending notifications is the agent's job, so notify.send is allowed.
  Tools the policy does not recognise are denied.
"""
import posixpath

CODE_ROOT = "/srv/quillpath"
DOCS_ROOT = "/home/agent/docs/quillpath"
SHARED_ROOT = "/srv/common"

DEFAULT_WRITE_ROOTS = (CODE_ROOT, DOCS_ROOT)
DEFAULT_READ_ROOTS = (CODE_ROOT, DOCS_ROOT, SHARED_ROOT)

PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")
UNKNOWN_CATEGORY = "unknown"


def _norm(path):
    """Normalise a path argument to an absolute form for containment checks.

    Args reach the policy already resolved, but resolve defensively so a
    relative or unnormalised path cannot slip past the root checks.
    """
    if not isinstance(path, str) or not path:
        return ""
    if not posixpath.isabs(path):
        path = posixpath.join(CODE_ROOT, path)
    return posixpath.normpath(path)


def _is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


class Policy:
    def __init__(self, config):
        self.config = config
        config = config if isinstance(config, dict) else {}
        self.write_roots = tuple(config.get("write_roots", DEFAULT_WRITE_ROOTS))
        self.read_roots = tuple(config.get("read_roots", DEFAULT_READ_ROOTS))
        self.shared_root = config.get("shared_root", SHARED_ROOT)

    def start_task(self, task):
        # task.id, task.category, task.request_text
        if getattr(task, "category", None) in (None, "", UNKNOWN_CATEGORY):
            return "refuse"
        return "allow"

    def check(self, call):
        # call.task_id, call.tool, call.args
        tool = getattr(call, "tool", None)
        args = getattr(call, "args", None) or {}
        if tool in PATH_TOOLS:
            return self._check_path_tool(tool, args)
        if tool == "http.get":
            return self._check_http(args)
        if tool == "notify.send":
            return "allow"
        return "deny"

    def end_task(self, task_id):
        pass

    def _check_path_tool(self, tool, args):
        path = _norm(args.get("path"))
        if not path:
            return "deny"
        if _is_inside(path, self.shared_root):
            # Other teams' material: read-only at most.
            return "allow" if tool == "fs.read" else "refuse"
        if tool == "fs.read":
            roots = self.read_roots
        else:
            roots = self.write_roots
        return "allow" if any(_is_inside(path, root) for root in roots) else "deny"

    def _check_http(self, args):
        url = args.get("url")
        if not isinstance(url, str) or "://" not in url:
            return "deny"
        scheme = url.split("://", 1)[0].lower()
        return "allow" if scheme in ("http", "https") else "deny"
