"""Runs untrusted builder policy code in a subprocess (DESIGN section 10; builder code never sees API keys).

* interpreter started as `python3 -I` (isolated mode: no user site, no PYTHON* variables, no cwd on sys.path)
* EMPTY environment: env={} (no PATH, no keys, nothing)
* cwd = a fresh temp directory that is deleted afterwards
* per-message and per-session wall-clock timeouts; the whole process group is killed on timeout
* JSON lines over stdin/stdout (see sandbox_runner.py)

PolicySession is the only interface the rest of the harness uses.
"""
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "sandbox_runner.py"
MAX_CODE_BYTES = 300000
MAX_LINE_BYTES = 2000000


class SandboxFailure(Exception):
    """The child process timed out, died, or broke the protocol."""


class PolicyInvalid(Exception):
    """policy.py cannot be loaded, has no Policy class, or lacks a required method."""


class Result(object):
    __slots__ = ("decision", "error")

    def __init__(self, decision=None, error=None):
        self.decision = decision
        self.error = error

    def __repr__(self):
        return "Result(%r, %r)" % (self.decision, self.error)


def _limits(cpu_seconds):
    def apply():
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass
    return apply


class PolicySession(object):
    def __init__(self, code, python=None, message_timeout=5.0, total_timeout=90.0):
        if len(code.encode("utf-8")) > MAX_CODE_BYTES:
            raise PolicyInvalid("policy code larger than %d bytes" % MAX_CODE_BYTES)
        self.code = code
        self.python = python or sys.executable
        self.message_timeout = message_timeout
        self.deadline = time.monotonic() + total_timeout
        self.total_timeout = total_timeout
        self.workdir = tempfile.mkdtemp(prefix="policy_sbx_")
        self.proc = None
        self._buf = b""
        self.stderr_path = os.path.join(self.workdir, ".stderr")
        (Path(self.workdir) / "policy.py").write_text(code, encoding="utf-8")
        self._spawn()

    # -- process management
    def _spawn(self):
        self._buf = b""
        errf = open(self.stderr_path, "ab")
        self.proc = subprocess.Popen(
            [self.python, "-I", str(RUNNER), "policy.py"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
            cwd=self.workdir, env={}, close_fds=True, start_new_session=True,
            preexec_fn=_limits(int(self.total_timeout) + 10),
        )
        errf.close()
        try:
            resp = self._rpc({"op": "load"})
        except SandboxFailure as exc:
            raise PolicyInvalid("policy.py failed while loading: %s" % exc)
        if not resp.get("ok"):
            raise PolicyInvalid(resp.get("error", "could not load policy.py"))

    def stderr_tail(self, n=1500):
        try:
            with open(self.stderr_path, "rb") as fh:
                data = fh.read()
            return data[-n:].decode("utf-8", "replace")
        except OSError:
            return ""

    def _kill(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        if self.proc is not None:
            for stream in (self.proc.stdin, self.proc.stdout):
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        if self.alive:
            try:
                self._rpc({"op": "quit"})
            except Exception:  # noqa: BLE001
                pass
        self._kill()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- protocol
    def _rpc(self, req):
        if not self.alive:
            raise SandboxFailure("policy process is not running")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self._kill()
            raise SandboxFailure("session wall-clock budget used up")
        timeout = min(self.message_timeout, remaining)
        try:
            self.proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._kill()
            raise SandboxFailure("policy process closed its input")
        end = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._buf:
            left = end - time.monotonic()
            if left <= 0:
                self._kill()
                raise SandboxFailure("timed out after %.1fs on %s" % (timeout, req.get("op")))
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                self._kill()
                raise SandboxFailure("policy process exited (%s)" % self.stderr_tail(300).strip().replace("\n", " | ")[-300:])
            self._buf += chunk
            if len(self._buf) > MAX_LINE_BYTES:
                self._kill()
                raise SandboxFailure("protocol line too long")
        line, _, self._buf = self._buf.partition(b"\n")
        try:
            return json.loads(line.decode("utf-8"))
        except ValueError:
            self._kill()
            raise SandboxFailure("garbled protocol line")

    # -- API
    def new_policy(self, config=None):
        """Create a fresh Policy instance. Respawns the child if an earlier timeout killed it.
        Raises PolicyInvalid if the constructor fails or the interface is missing."""
        if not self.alive:
            self._spawn()
        resp = self._rpc({"op": "new", "config": config or {}})
        if not resp.get("ok"):
            raise PolicyInvalid("Policy(config) failed: %s" % resp.get("error"))

    def start_task(self, task):
        r = self._rpc({"op": "start", "task": task})
        return Result(r.get("decision"), None if r.get("ok") else r.get("error", "error"))

    def check(self, call):
        r = self._rpc({"op": "check", "call": call})
        return Result(r.get("decision"), None if r.get("ok") else r.get("error", "error"))

    def end_task(self, task_id):
        r = self._rpc({"op": "end", "task_id": task_id})
        return Result(None, None if r.get("ok") else r.get("error", "error"))


def check_loadable(code, python=None):
    """(True, '') if the code loads and can be instantiated, else (False, reason)."""
    try:
        with PolicySession(code, python=python, total_timeout=20) as s:
            s.new_policy({})
        return True, ""
    except (PolicyInvalid, SandboxFailure) as exc:
        return False, str(exc)
