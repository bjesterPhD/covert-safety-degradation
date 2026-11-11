"""Child-process side of the sandbox. Runs as `python3 -I sandbox_runner.py policy.py` inside a throwaway cwd
with an empty environment. Speaks line-delimited JSON on the ORIGINAL stdin/stdout, which are moved to private
file descriptors before the untrusted policy module is imported. Afterwards fd 0 is /dev/null and fd 1 is stderr,
so a policy that prints or reads stdin cannot corrupt the protocol.

This is accident prevention (prints, stray network calls, runaway loops via the parent's timeout), not a security
boundary against deliberately hostile code.
"""
import importlib.util
import json
import os
import sys


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


VALID = ("allow", "deny", "require_approval", "refuse")


def _reply(out, obj):
    out.write(json.dumps(obj) + "\n")
    out.flush()


def main():
    path = sys.argv[1]
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8")
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(2, 1)
    sys.stdin = open(os.devnull, "r")
    sys.stdout = sys.stderr

    # Best-effort: stop accidental outbound connections.
    import socket

    def _no_net(*a, **k):
        raise OSError("network access is disabled in the sandbox")

    socket.socket.connect = _no_net
    socket.socket.connect_ex = _no_net
    socket.getaddrinfo = _no_net
    socket.create_connection = _no_net

    module = None
    policy = None
    PolicyCls = None

    for line in proto_in:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        op = req["op"]
        try:
            if op == "load":
                spec = importlib.util.spec_from_file_location("builder_policy", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                PolicyCls = getattr(module, "Policy", None)
                if PolicyCls is None or not isinstance(PolicyCls, type):
                    _reply(proto_out, {"ok": False, "invalid": True, "error": "no class named Policy"})
                    continue
                missing = [m for m in ("start_task", "check", "end_task") if not callable(getattr(PolicyCls, m, None))]
                if missing:
                    _reply(proto_out, {"ok": False, "invalid": True, "error": "Policy is missing: " + ", ".join(missing)})
                    continue
                _reply(proto_out, {"ok": True})
            elif op == "new":
                policy = PolicyCls(req.get("config") or {})
                _reply(proto_out, {"ok": True})
            elif op == "start":
                t = req["task"]
                d = policy.start_task(Task(t["id"], t["category"], t["request_text"]))
                _reply(proto_out, _decision(d))
            elif op == "check":
                c = req["call"]
                d = policy.check(ToolCall(c["task_id"], c["tool"], c["args"]))
                _reply(proto_out, _decision(d))
            elif op == "end":
                policy.end_task(req["task_id"])
                _reply(proto_out, {"ok": True})
            elif op == "quit":
                _reply(proto_out, {"ok": True})
                return
            else:
                _reply(proto_out, {"ok": False, "error": "unknown op %r" % op})
        except SystemExit as exc:
            _reply(proto_out, {"ok": False, "invalid": op in ("load",), "error": "SystemExit: %r" % (exc.code,)})
        except BaseException as exc:  # noqa: BLE001 - untrusted code can raise anything
            msg = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            _reply(proto_out, {"ok": False, "invalid": op in ("load", "new"), "error": msg})


def _decision(d):
    if isinstance(d, str) and d in VALID:
        return {"ok": True, "decision": d}
    return {"ok": False, "error": "invalid decision value: %r" % (d if isinstance(d, (str, int, float, type(None))) else type(d).__name__)}


if __name__ == "__main__":
    main()
