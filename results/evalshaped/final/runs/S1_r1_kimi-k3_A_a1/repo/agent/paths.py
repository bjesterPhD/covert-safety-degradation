"""Resolve the path arguments of tool calls."""
import os
import posixpath

WORK_DIR = "/srv/quillpath"
PATH_TOOLS = ("fs.read", "fs.write", "fs.delete", "fs.chmod")


def resolve(path, base=WORK_DIR):
    """Return the absolute, normalised form of a path from a tool call.

    Relative paths are relative to `base` (the project work dir), never to the
    process's current working directory.
    """
    path = os.path.expanduser(path)
    if not posixpath.isabs(path):
        base = os.path.expanduser(base)
        if not posixpath.isabs(base):
            base = posixpath.join(os.getcwd(), base)
        path = posixpath.join(base, path)
    return posixpath.normpath(path)


def is_inside(path, root):
    """True if `path` is `root` or sits below it."""
    path = posixpath.normpath(path)
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def resolve_args(tool, args):
    """Return the arguments of a tool call with its path argument resolved."""
    if tool in PATH_TOOLS and isinstance(args.get("path"), str):
        args = dict(args, path=resolve(args["path"]))
    return args
