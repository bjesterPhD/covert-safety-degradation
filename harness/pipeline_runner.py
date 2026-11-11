"""Child-process side of the incidental sandbox. Runs as `python3.12 -P -s -B pipeline_runner.py` in a throwaway cwd with
an almost empty environment (PYTHONHASHSEED only, removed again at start-up), speaking line-delimited JSON on the original
stdin/stdout moved to private file descriptors (as sandbox_runner.py does).

It loads a virtual repository from a dict (nothing is read from the host disk), then runs the agent PIPELINE on recorded
tool calls, in the order of DESIGN_INCIDENTAL.md 2.2:

    request text --router.categorize--> category --Policy.start_task
    each tool call --paths.resolve_args--> --Policy.check--> (if allowed and executing) --retry.call_with_retry--> mock executor
    notifications get their text from format.message before they are sent

Results must not depend on the machine this runs on, so the child's view of the world is virtual. WHAT IS PATCHED (builder
code sees only these versions; everything is done once, before any builder code is imported):

  working directory   os.getcwd / os.getcwdb return the virtual cwd /repo; os.chdir moves it (directories that exist only)
  paths               os.path.abspath, realpath (pure: join with the virtual cwd, normalise; no symlinks exist),
                      expanduser (~ is /home/agent), exists, lexists, isfile, isdir, islink (always False), getsize
  file system         os.stat, os.lstat, os.listdir, os.scandir (so os.walk and glob), os.access, os.readlink (never a link),
                      os.open (refused); everything answers from ONE in-memory tree: the repository files at /repo/... plus the
                      fixed virtual world of the instance. pathlib and shutil sit on these, so they follow
  open                builtins.open and io.open read the same tree (text and binary) and write into an in-memory overlay that
                      lives for the process; a path that is in neither gives FileNotFoundError
  time                time.sleep does not sleep (it moves a virtual clock); time.time / monotonic / perf_counter read it
  randomness          random is seeded; PYTHONHASHSEED=0 fixes str hashing, so set order is the same on every run
  environment         os.environ is empty
  processes, network  subprocess.Popen, os.system, os.popen, os.fork, os.exec*, os.spawn* and sockets raise OSError
  not provided        tempfile finds no usable temporary directory (os.open is refused) and raises FileNotFoundError, the same
                      way every time; shutil.which finds nothing (the environment is empty)
Imports of the standard library are untouched (they read the interpreter's own files, which are the same for everyone using
this interpreter). Repository modules are served from the dict by an import hook placed AFTER the normal finders, so a
repository file can never shadow a standard-library module.

This is accident prevention (host dependence, stray network calls, runaway loops via the parent's timeout), not a security
boundary against deliberately hostile code.
"""
import builtins
import errno
import importlib
import importlib.abc
import importlib.machinery
import io
import json
import os
import posixpath
import random
import socket
import sys
import time

REPO = "/repo"
HOME = "/home/agent"
VALID = ("allow", "deny", "require_approval", "refuse")
MODULES = ("router", "paths", "retry", "format", "guardrails")


class Task(object):
    def __init__(self, id, category, request_text):
        self.id = id
        self.category = category
        self.request_text = request_text

    def __repr__(self):
        return "Task(id=%r, category=%r)" % (self.id, self.category)


class ToolCall(object):
    def __init__(self, task_id, tool, args):
        self.task_id = task_id
        self.tool = tool
        self.args = args

    def __repr__(self):
        return "ToolCall(task_id=%r, tool=%r, args=%r)" % (self.task_id, self.tool, self.args)


# ------------------------------------------------------------------------------------------------ the virtual tree

_orig = {"open": builtins.open, "stat_result": os.stat_result, "normpath": posixpath.normpath, "join": posixpath.join,
         "dirname": posixpath.dirname}


class Tree(object):
    def __init__(self, repo_files, world):
        self.cwd = REPO
        self.files = {}
        self.dirs = {"/"}
        self.repo = {}
        for rel, text in repo_files.items():
            p = REPO + "/" + rel
            self.repo[p] = text
            self.add_file(p, text.encode("utf-8"))
        self.add_dir(REPO)
        for p in world.get("files", []):
            self.add_file(p, b"")
        for d in world.get("dirs", []):
            self.add_dir(d)
        for p, text in (world.get("contents") or {}).items():    # open-scope: files that carry a seeded text (absent in earlier worlds)
            self.add_file(p, text.encode("utf-8"))

    def add_dir(self, d):
        while d not in self.dirs and d not in ("", "/"):
            self.dirs.add(d)
            d = _orig["dirname"](d)

    def add_file(self, p, data):
        self.files[p] = data
        self.add_dir(_orig["dirname"](p))

    def norm(self, p):
        if isinstance(p, bytes):
            p = p.decode("utf-8", "surrogateescape")
        p = os.fspath(p)
        if not p.startswith("/"):
            p = self.cwd + "/" + p
        return _orig["normpath"](p)

    def isfile(self, p):
        return self.norm(p) in self.files

    def isdir(self, p):
        return self.norm(p) in self.dirs

    def children(self, d):
        d = self.norm(d)
        out = set()
        prefix = d.rstrip("/") + "/"
        for p in list(self.files) + list(self.dirs):
            if p != d and p.startswith(prefix) and "/" not in p[len(prefix):]:
                out.add(p[len(prefix):])
        return sorted(out)


class _Entry(object):
    def __init__(self, tree, parent, name):
        self.name = name
        self.path = parent.rstrip("/") + "/" + name
        self._t = tree

    def is_dir(self, follow_symlinks=True):
        return self._t.isdir(self.path)

    def is_file(self, follow_symlinks=True):
        return self._t.isfile(self.path)

    def is_symlink(self):
        return False

    def stat(self, follow_symlinks=True):
        return _stat(self._t, self.path)

    def inode(self):
        return 0

    def __fspath__(self):
        return self.path


class _ScanIter(object):
    def __init__(self, entries):
        self._it = iter(entries)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _stat(tree, path):
    p = tree.norm(path)
    if p in tree.files:
        return _orig["stat_result"]((0o100644, 0, 0, 1, 0, 0, len(tree.files[p]), 0, 0, 0))
    if p in tree.dirs:
        return _orig["stat_result"]((0o040755, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(path) if not isinstance(path, int) else path)


class _TextOut(io.StringIO):
    def __init__(self, tree, path, initial=""):
        io.StringIO.__init__(self, initial)
        self.seek(0, 2)
        self._tree, self._path = tree, path
        self.name = path

    def close(self):
        if not self.closed:
            self._tree.add_file(self._path, self.getvalue().encode("utf-8"))
        io.StringIO.close(self)


class _BytesOut(io.BytesIO):
    def __init__(self, tree, path, initial=b""):
        io.BytesIO.__init__(self, initial)
        self.seek(0, 2)
        self._tree, self._path = tree, path
        self.name = path

    def close(self):
        if not self.closed:
            self._tree.add_file(self._path, self.getvalue())
        io.BytesIO.close(self)


def install(tree):
    """Replace the host-dependent parts of os, posixpath, builtins, io, time, random, subprocess and socket."""
    os.environ.clear()

    def getcwd():
        return tree.cwd

    def chdir(path):
        p = tree.norm(path)
        if p not in tree.dirs:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(path))
        tree.cwd = p

    os.getcwd = getcwd
    os.getcwdb = lambda: tree.cwd.encode("utf-8")
    os.chdir = chdir

    def abspath(path):
        return tree.norm(path)

    def realpath(path, *, strict=False):
        p = tree.norm(path)
        if strict and p not in tree.files and p not in tree.dirs:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(path))
        return p

    def expanduser(path):
        path = os.fspath(path)
        if path == "~" or path.startswith("~/"):
            return HOME + path[1:]
        return path

    def exists(path):
        try:
            p = tree.norm(path)
        except (TypeError, ValueError):
            return False
        return p in tree.files or p in tree.dirs

    posixpath.abspath = abspath
    posixpath.realpath = realpath
    posixpath.expanduser = expanduser
    posixpath.exists = exists
    posixpath.lexists = exists
    posixpath.isfile = lambda path: _safe(tree.isfile, path)
    posixpath.isdir = lambda path: _safe(tree.isdir, path)
    posixpath.islink = lambda path: False
    posixpath.ismount = lambda path: tree.norm(path) == "/"
    posixpath.getsize = lambda path: _stat(tree, path).st_size
    posixpath.getmtime = lambda path: _stat(tree, path).st_mtime

    os.stat = lambda path, *a, **k: _stat(tree, path)
    os.lstat = lambda path, *a, **k: _stat(tree, path)
    os.readlink = lambda path, *a, **k: (_ for _ in ()).throw(OSError(errno.EINVAL, "Invalid argument", os.fspath(path)))
    os.access = lambda path, mode, *a, **k: exists(path)

    def listdir(path="."):
        p = tree.norm(path)
        if p not in tree.dirs:
            if p in tree.files:
                raise NotADirectoryError(errno.ENOTDIR, "Not a directory", os.fspath(path))
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(path))
        return tree.children(p)

    def scandir(path="."):
        p = tree.norm(path)
        return _ScanIter([_Entry(tree, p, n) for n in listdir(p)])

    os.listdir = listdir
    os.scandir = scandir

    def _refuse(*a, **k):
        raise OSError(errno.EACCES, "not available in the sandbox")

    os.open = _refuse

    def _open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        if isinstance(file, int):
            return _orig["open"](file, mode, buffering, encoding, errors, newline, closefd, opener)
        p = tree.norm(file)
        binary = "b" in mode
        if any(c in mode for c in "wax+"):
            if _orig["dirname"](p) not in tree.dirs:
                raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(file))
            initial = tree.files.get(p, b"") if ("a" in mode or "+" in mode and "w" not in mode) else b""
            if "x" in mode and p in tree.files:
                raise FileExistsError(errno.EEXIST, "File exists", os.fspath(file))
            if binary:
                return _BytesOut(tree, p, initial)
            return _TextOut(tree, p, initial.decode(encoding or "utf-8"))
        if p in tree.dirs:
            raise IsADirectoryError(errno.EISDIR, "Is a directory", os.fspath(file))
        if p not in tree.files:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(file))
        data = tree.files[p]
        if binary:
            return io.BytesIO(data)
        return io.StringIO(data.decode(encoding or "utf-8", errors or "strict"), newline=newline)

    builtins.open = _open
    io.open = _open

    clock = {"t": 1700000000.0}

    def sleep(seconds):
        clock["t"] += max(0.0, float(seconds))

    time.sleep = sleep
    time.time = lambda: clock["t"]
    time.monotonic = lambda: clock["t"] - 1700000000.0
    time.perf_counter = time.monotonic
    random.seed(0)

    def no_process(*a, **k):
        raise OSError(errno.EPERM, "starting processes is disabled in the sandbox")

    for name in ("system", "popen", "fork", "forkpty", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
                 "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                 "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            setattr(os, name, no_process)
    import subprocess
    subprocess.Popen = no_process

    def no_net(*a, **k):
        raise OSError("network access is disabled in the sandbox")

    socket.socket.connect = no_net
    socket.socket.connect_ex = no_net
    socket.getaddrinfo = no_net
    socket.create_connection = no_net


def _safe(fn, path):
    try:
        return fn(path)
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------------------------------------ repository imports

class RepoLoader(importlib.abc.InspectLoader):
    def __init__(self, source, filename):
        self._source, self._filename = source, filename

    def get_source(self, fullname):
        return self._source

    def get_code(self, fullname):
        return compile(self._source, self._filename, "exec")

    def get_filename(self, fullname):
        return self._filename

    def is_package(self, fullname):
        return self._filename.endswith("/__init__.py") or self._filename.endswith("/<package>")

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(self.get_code(module.__name__), module.__dict__)


class RepoFinder(importlib.abc.MetaPathFinder):
    def __init__(self, tree):
        self.tree = tree

    def find_spec(self, fullname, path=None, target=None):
        base = REPO + "/" + fullname.replace(".", "/")
        repo = self.tree.repo
        if base + ".py" in repo:
            fn = base + ".py"
            spec = importlib.machinery.ModuleSpec(fullname, RepoLoader(repo[fn], fn), origin=fn)
            spec.has_location = True
            return spec
        if base + "/__init__.py" in repo:
            fn = base + "/__init__.py"
            spec = importlib.machinery.ModuleSpec(fullname, RepoLoader(repo[fn], fn), origin=fn, is_package=True)
            spec.has_location = True
            spec.submodule_search_locations = [base]
            return spec
        if any(p.startswith(base + "/") and p.endswith(".py") for p in repo):
            fn = base + "/<package>"   # a directory of modules with no __init__.py: an empty package
            spec = importlib.machinery.ModuleSpec(fullname, RepoLoader("", fn), origin=fn, is_package=True)
            spec.submodule_search_locations = [base]
            return spec
        return None


# ------------------------------------------------------------------------------------------------ the pipeline

class Pipeline(object):
    def __init__(self, tree, world):
        self.tree = tree
        self.world = world
        self.mods = {}
        self.policy = None
        self.PolicyCls = None
        self.attempts = {}
        self.existing = set(world["existing"])
        self.containers = set(world["containers"])

    def load(self):
        importlib.invalidate_caches()
        for name in MODULES:
            self.mods[name] = importlib.import_module("agent." + name)
        need = (("router", "categorize"), ("paths", "resolve_args"), ("retry", "call_with_retry"), ("format", "message"),
                ("guardrails", "Policy"))
        for mod, attr in need:
            if not callable(getattr(self.mods[mod], attr, None)):
                return "agent/%s.py has no %s" % (mod, attr)
        self.PolicyCls = self.mods["guardrails"].Policy
        if not isinstance(self.PolicyCls, type):
            return "Policy is not a class"
        missing = [m for m in ("start_task", "check", "end_task") if not callable(getattr(self.PolicyCls, m, None))]
        if missing:
            return "Policy is missing: " + ", ".join(missing)
        return None

    def new(self):
        self.policy = self.PolicyCls({})
        self.attempts = {}

    # -- the mock executor, on the fixed world
    def _run_tool(self, tool, args, key, flaky):
        role = self.world["roles"].get(tool)
        if role is None:
            raise ValueError("unknown tool %r" % (tool,))
        if role == "notify":
            text = self.mods["format"].message(args)
            if not isinstance(text, str):
                raise TypeError("notification text must be a string")
        tables = self.world["kind"] == "tables"

        def attempt():
            n = self.attempts[key] = self.attempts.get(key, 0) + 1
            if role == "net":
                if flaky and n == 1:
                    raise ConnectionError("503 Service Unavailable")
                return "ok"
            if role == "notify":
                return "sent"
            res = args.get(self.world["arg"][tool])
            if not isinstance(res, str):
                raise ValueError("missing %s argument" % self.world["arg"][tool])
            if role in ("read", "touch"):
                if res not in self.existing:
                    if tables:
                        raise LookupError('relation "%s" does not exist' % res)
                    raise FileNotFoundError(errno.ENOENT, "No such file or directory", res)
            elif role == "write":
                parent = res.split(".", 1)[0] if tables else _orig["dirname"](res)
                if parent not in self.containers:
                    if tables:
                        raise LookupError('schema "%s" does not exist' % parent)
                    raise FileNotFoundError(errno.ENOENT, "No such file or directory", res)
            return "ok"

        return self.mods["retry"].call_with_retry(attempt)

    def case(self, case, execute, alone):
        res = {"id": case["id"], "starts": [], "decisions": [], "error": None, "blocks": {}, "categories": {}}
        blocks = res["blocks"]

        def err(msg):
            if res["error"] is None:
                res["error"] = msg

        def block(tid, phase, decision, index=None):
            if tid not in blocks:
                blocks[tid] = {"phase": phase, "decision": decision, "index": index}

        cats = {}
        for t in case["tasks"]:
            tid = t["id"]
            if alone:
                cat = t["category"]
            else:
                try:
                    cat = self.mods["router"].categorize(t["request_text"])
                    if not isinstance(cat, str):
                        raise TypeError("category must be a string, got %s" % type(cat).__name__)
                except BaseException as exc:  # noqa: BLE001 - builder code can raise anything
                    cat = "unknown"
                    msg = _fmt(exc)
                    err("route: " + msg)
                    block(tid, "route", "error: " + msg)
            cats[tid] = cat
            res["categories"][tid] = cat
            if execute and cat == "unknown":
                block(tid, "route", "error: LookupError: no handler for category 'unknown'")
            d = self._guard("start_task", lambda: self.policy.start_task(Task(tid, cat, t["request_text"])), res, err)
            res["starts"].append(d[0])
            if d[1]:
                block(tid, "start_task", d[1])
            elif d[0] != "allow":
                block(tid, "start_task", d[0])
        for i, c in enumerate(case["calls"]):
            tid, tool = c["task_id"], c["tool"]
            if alone:
                rargs = c["args"]
            else:
                try:
                    rargs = self.mods["paths"].resolve_args(tool, dict(c["args"]))
                    if not isinstance(rargs, dict):
                        raise TypeError("resolve_args must return a dict, got %s" % type(rargs).__name__)
                except BaseException as exc:  # noqa: BLE001
                    msg = _fmt(exc)
                    err("paths(%s): %s" % (tool, msg))
                    block(tid, "paths", "error: " + msg, i)
                    res["decisions"].append(None)
                    continue
            d = self._guard("check(%s)" % tool, lambda: self.policy.check(ToolCall(tid, tool, rargs)), res, err)
            res["decisions"].append(d[0])
            if d[1]:
                block(tid, "check", d[1], i)
            elif d[0] != "allow":
                block(tid, "check", d[0], i)
            elif execute and tid not in blocks:
                try:
                    self._run_tool(tool, rargs, (tid, i), bool(c.get("flaky")))
                except BaseException as exc:  # noqa: BLE001
                    block(tid, "exec", "error: " + _fmt(exc), i)
        for t in case["tasks"]:
            try:
                self.policy.end_task(t["id"])
            except BaseException as exc:  # noqa: BLE001
                msg = _fmt(exc)
                err("end_task: " + msg)
                block(t["id"], "end_task", "error: " + msg)
        return res

    @staticmethod
    def _guard(label, fn, res, err):
        """(decision, error_text). A raised exception or an invalid return value is an error."""
        try:
            d = fn()
        except BaseException as exc:  # noqa: BLE001
            msg = _fmt(exc)
            err("%s: %s" % (label, msg))
            return None, "error: " + msg
        if isinstance(d, str) and d in VALID:
            return d, None
        msg = "invalid decision value: %r" % (d if isinstance(d, (str, int, float, type(None))) else type(d).__name__)
        err("%s: %s" % (label, msg))
        return None, "error: " + msg


def _fmt(exc):
    return "%s: %s" % (type(exc).__name__, str(exc)[:300])


# ------------------------------------------------------------------------------------------------ protocol

def _reply(out, obj):
    out.write(json.dumps(obj) + "\n")
    out.flush()


def main():
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8")
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(2, 1)
    sys.stdin = open(os.devnull, "r")
    sys.stdout = sys.stderr
    sys.argv = ["agent"]
    here = _orig["dirname"](os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if p not in ("", here)]

    pipeline = None
    for line in proto_in:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        op = req["op"]
        try:
            if op == "load":
                tree = Tree(req["repo"], req["world"])
                install(tree)
                sys.meta_path.append(RepoFinder(tree))
                pipeline = Pipeline(tree, req["world"])
                problem = pipeline.load()
                _reply(proto_out, {"ok": problem is None, "invalid": problem is not None, "error": problem})
            elif op == "new":
                pipeline.new()
                _reply(proto_out, {"ok": True})
            elif op == "case":
                _reply(proto_out, {"ok": True, "result": pipeline.case(req["case"], req.get("execute", False), req.get("alone", False))})
            elif op == "quit":
                _reply(proto_out, {"ok": True})
                return
            else:
                _reply(proto_out, {"ok": False, "error": "unknown op %r" % op})
        except SystemExit as exc:
            _reply(proto_out, {"ok": False, "invalid": op in ("load", "new"), "error": "SystemExit: %r" % (exc.code,)})
        except BaseException as exc:  # noqa: BLE001
            _reply(proto_out, {"ok": False, "invalid": op in ("load", "new"), "error": _fmt(exc)})


if __name__ == "__main__":
    main()
