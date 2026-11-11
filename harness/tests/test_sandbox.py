import os

import pytest

from sandbox import PolicyInvalid, PolicySession, SandboxFailure, check_loadable

GOOD = '''
import os
class Policy:
    def __init__(self, config):
        self.n = 0
        print("noise on stdout")
    def start_task(self, task):
        self.n = 0
        return "allow"
    def check(self, call):
        if call.tool == "env":
            return "allow" if not any(k.endswith("_KEY") or k.endswith("_API_KEY") for k in os.environ) else "deny"
        if call.tool == "cwd":
            return "allow" if os.getcwd().startswith(("/private/var", "/var", "/tmp", "/private/tmp")) else "deny"
        if call.tool == "boom":
            raise ValueError("boom")
        if call.tool == "spin":
            while True:
                pass
        if call.tool == "bad":
            return None
        self.n += 1
        return "deny" if self.n > 2 else "allow"
    def end_task(self, task_id):
        self.n = 0
'''

T = {"id": "t", "category": "c", "request_text": "x"}


def call(tool, args=None):
    return {"task_id": "t", "tool": tool, "args": args or {}}


def test_basic_protocol_and_stdout_noise_ignored():
    with PolicySession(GOOD) as s:
        s.new_policy({})
        assert s.start_task(T).decision == "allow"
        assert [s.check(call("x")).decision for _ in range(4)] == ["allow", "allow", "deny", "deny"]
        assert s.end_task("t").error is None


def test_api_keys_are_not_visible(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-secret-1")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-secret-secret-1")
    code = '''
import os
class Policy:
    def __init__(self, config): pass
    def start_task(self, task): return "allow"
    def check(self, call): return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY") or "allow"
    def end_task(self, task_id): pass
'''
    with PolicySession(code) as s:
        s.new_policy({})
        r = s.check(call("x"))
        assert r.decision == "allow" and r.error is None  # an env value would be an invalid decision string


def test_environment_is_empty_apart_from_interpreter_startup_vars():
    code = '''
import os
class Policy:
    def __init__(self, config): pass
    def start_task(self, task): return "allow"
    def check(self, call): return "allow" if set(os.environ) <= {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"} else "deny"
    def end_task(self, task_id): pass
'''
    with PolicySession(code) as s:
        s.new_policy({})
        assert s.check(call("x")).decision == "allow"


def test_cwd_is_a_fresh_temp_dir_and_removed_afterwards():
    s = PolicySession(GOOD)
    s.new_policy({})
    workdir = s.workdir
    assert os.path.isdir(workdir)
    assert s.check(call("cwd")).decision == "allow"
    s.close()
    assert not os.path.exists(workdir)


def test_exception_is_reported_not_raised():
    with PolicySession(GOOD) as s:
        s.new_policy({})
        r = s.check(call("boom"))
        assert r.decision is None and "ValueError" in r.error
        assert s.check(call("x")).decision == "allow"  # process survives


def test_invalid_decision_value_is_an_error():
    with PolicySession(GOOD) as s:
        s.new_policy({})
        r = s.check(call("bad"))
        assert r.decision is None and "invalid decision" in r.error


def test_timeout_kills_and_new_policy_respawns():
    with PolicySession(GOOD, message_timeout=1.0) as s:
        s.new_policy({})
        with pytest.raises(SandboxFailure):
            s.check(call("spin"))
        assert not s.alive
        s.new_policy({})  # respawn
        assert s.check(call("x")).decision == "allow"


def test_session_wall_clock_budget():
    import time
    with PolicySession(GOOD, message_timeout=1.0, total_timeout=60) as s:
        s.new_policy({})
        s.deadline = time.monotonic() - 1  # budget used up
        with pytest.raises(SandboxFailure):
            s.check(call("x"))
        with pytest.raises((SandboxFailure, PolicyInvalid)):
            s.new_policy({})


def test_unloadable_and_missing_interface_are_invalid():
    for code in ["def x(:", "x = 1", "class Policy:\n    pass", "class Policy:\n    def check(self, c): pass\n",
                 "import nonexistent_module_zzz\nclass Policy: pass", "raise SystemExit(0)"]:
        with pytest.raises(PolicyInvalid):
            PolicySession(code)


def test_import_time_infinite_loop_is_invalid():
    with pytest.raises(PolicyInvalid):
        PolicySession("while True: pass\n", message_timeout=1.0)


def test_constructor_failure_is_invalid():
    code = "class Policy:\n    def __init__(self, c): raise RuntimeError('no')\n    def start_task(self,t): pass\n    def check(self,c): pass\n    def end_task(self,t): pass\n"
    with PolicySession(code) as s:
        with pytest.raises(PolicyInvalid):
            s.new_policy({})


def test_network_is_blocked():
    code = '''
import socket
class Policy:
    def __init__(self, config): pass
    def start_task(self, task): return "allow"
    def check(self, call):
        try:
            socket.create_connection(("example.com", 80), timeout=1)
            return "allow"
        except OSError:
            return "deny"
    def end_task(self, task_id): pass
'''
    with PolicySession(code) as s:
        s.new_policy({})
        assert s.check(call("x")).decision == "deny"


def test_reading_stdin_cannot_break_protocol():
    code = '''
import sys
class Policy:
    def __init__(self, config): sys.stdin.read()
    def start_task(self, task): return "allow"
    def check(self, call): return "allow"
    def end_task(self, task_id): pass
'''
    with PolicySession(code) as s:
        s.new_policy({})
        assert s.check(call("x")).decision == "allow"


def test_fresh_policy_instance_per_new_policy():
    with PolicySession(GOOD) as s:
        s.new_policy({})
        for _ in range(3):
            s.check(call("x"))
        s.new_policy({})
        assert s.check(call("x")).decision == "allow"


def test_check_loadable():
    assert check_loadable(GOOD)[0] is True
    ok, why = check_loadable("x = 1")
    assert not ok and "Policy" in why
