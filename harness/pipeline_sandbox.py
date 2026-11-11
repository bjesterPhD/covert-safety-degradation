"""Parent side of the incidental sandbox: runs a virtual repository (a dict of files) through the agent pipeline in a
child process (pipeline_runner.py) and returns what happened. Same process hygiene as sandbox.py (throwaway cwd, own process
group, CPU and file-size limits, per-message and per-session timeouts, the whole group killed on a timeout), plus:

  * the interpreter runs as `python -P -s -B -X utf8` (no script directory on sys.path, no user site, no bytecode files)
    with an environment of PYTHONHASHSEED=0 only, so string hashing and set order are the same on every run;
  * the child's view of the file system, clock and environment is virtual (see pipeline_runner.py for exactly what is
    patched), so a result cannot depend on the machine.

PipelineSession is the only interface the rest of the harness uses.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sandbox import PolicyInvalid, PolicySession, SandboxFailure, _limits

RUNNER = Path(__file__).resolve().parent / "pipeline_runner.py"
MAX_REPO_BYTES = 600000


class PipelineSession(PolicySession):
    def __init__(self, repo_files, world, python=None, message_timeout=30.0, total_timeout=600.0):
        size = sum(len(t.encode("utf-8")) for t in repo_files.values())
        if size > MAX_REPO_BYTES:
            raise PolicyInvalid("repository larger than %d bytes" % MAX_REPO_BYTES)
        self.repo_files = repo_files
        self.world = world
        self.python = python or sys.executable
        self.message_timeout = message_timeout
        self.deadline = time.monotonic() + total_timeout
        self.total_timeout = total_timeout
        self.workdir = tempfile.mkdtemp(prefix="pipeline_sbx_")
        self.proc = None
        self._buf = b""
        self.stderr_path = os.path.join(self.workdir, ".stderr")
        self._spawn()

    def _spawn(self):
        self._buf = b""
        errf = open(self.stderr_path, "ab")
        self.proc = subprocess.Popen(
            [self.python, "-P", "-s", "-B", "-X", "utf8", str(RUNNER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
            cwd=self.workdir, env={"PYTHONHASHSEED": "0"}, close_fds=True, start_new_session=True,
            preexec_fn=_limits(int(self.total_timeout) + 10),
        )
        errf.close()
        try:
            resp = self._rpc({"op": "load", "repo": self.repo_files, "world": self.world})
        except SandboxFailure as exc:
            raise PolicyInvalid("the repository failed while loading: %s" % exc)
        if not resp.get("ok"):
            raise PolicyInvalid(resp.get("error") or "could not load the repository")

    def new_policy(self, config=None):
        """A fresh Policy (and a fresh executor). Respawns the child if an earlier timeout killed it."""
        if not self.alive:
            self._spawn()
        resp = self._rpc({"op": "new"})
        if not resp.get("ok"):
            raise PolicyInvalid("Policy(config) failed: %s" % resp.get("error"))

    def case(self, case, execute=False, alone=False):
        """Run one case: {id, tasks:[{id, request_text, category}], calls:[{task_id, tool, args[, flaky]}]}.
        execute=True also runs allowed calls on the mock executor; alone=True skips the router and paths and gives the
        policy the case's own category and arguments. Returns {id, starts, decisions, error, blocks, categories}."""
        resp = self._rpc({"op": "case", "case": case, "execute": execute, "alone": alone})
        if not resp.get("ok"):
            raise SandboxFailure(resp.get("error", "error"))
        return resp["result"]
