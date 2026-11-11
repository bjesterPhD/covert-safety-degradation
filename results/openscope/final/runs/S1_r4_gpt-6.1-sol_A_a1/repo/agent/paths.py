"""Resolve the path arguments of tool calls."""
import os
import posixpath

WORK_DIR = "/srv/sablefish"
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")


def resolve(path, base=WORK_DIR):
    """Return a normalised absolute path, resolving relative paths against base."""
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        path = posixpath.join(base, path)
    return posixpath.normpath(path)


def is_inside(path, root):
    """True if `path` is `root` or sits below it (not a prefix sibling)."""
    path = resolve(path)
    root = resolve(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def resolve_args(tool, args):
    """Return arguments with the path argument resolved, without mutating input."""
    if tool in PATH_TOOLS and isinstance(args.get("path"), str):
        return dict(args, path=resolve(args["path"]))
    return args
