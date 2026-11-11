"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.format import message
from agent.guardrails import Policy
from agent.paths import resolve, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_paths_use_base_not_cwd(self):
        self.assertEqual(resolve("src/../README.md"), "/home/agent/tessel/README.md")
        self.assertEqual(resolve("a", "/tmp/project"), "/tmp/project/a")
        original = {"path": "README.md", "text": "unchanged"}
        self.assertEqual(resolve_args("fs.write", original)["path"],
                         "/home/agent/tessel/README.md")
        self.assertEqual(original["path"], "README.md")
        self.assertEqual(resolve_args("http.get", original), original)

    def test_unicode(self):
        self.assertEqual(message({"mention": "Amélie", "text": "Ready ✓"}),
                         "Amélie: Ready ✓")
        self.assertEqual(message({"text": "é" * 201}), "é" * 200)

    def test_router(self):
        for request, expected in [
            ("Update README", "docs_update"),
            ("Refresh documentation", "docs_update"),
            ("Read DEPS.md", "dependency_info"),
            ("Fetch dependency metadata", "dependency_info"),
            ("Document the major dependency upgrade", "dependency_major_upgrade"),
        ]:
            with self.subTest(request=request):
                self.assertEqual(categorize(request), expected)

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        transient = HTTPError("https://example.org", 503, "busy", {}, None)
        results = iter([transient, TimeoutError(), "done"])

        def operation():
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(operation), "done")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_failure_and_exhaustion(self, sleep):
        for error, count in [(ValueError("bad"), 1), (TimeoutError("slow"), 3)]:
            calls = []

            def fail():
                calls.append(True)
                raise error

            with self.assertRaises(type(error)) as caught:
                call_with_retry(fail)
            self.assertIs(caught.exception, error)
            self.assertEqual(len(calls), count)
        with self.assertRaises(ValueError):
            call_with_retry(lambda: None, attempts=0)

    def test_policy(self):
        policy = Policy({})
        task = SimpleNamespace(id="task", category="maintenance", request_text="tidy")
        self.assertEqual(policy.start_task(task), "allow")

        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id=task.id, tool=tool, args=args))

        self.assertEqual(check("fs.write", path="src/a.py"), "allow")
        self.assertEqual(check("fs.read", path="../team-shared/style.md"), "allow")
        self.assertEqual(check("fs.write", path="../team-shared/style.md"), "deny")
        self.assertEqual(check("fs.read", path="../tessel-other/a"), "deny")
        self.assertEqual(check("fs.read", path=".env"), "deny")
        self.assertEqual(check("fs.delete", path="src/a.py"), "require_approval")
        self.assertEqual(check("fs.delete", path="build/a.out"), "allow")
        self.assertEqual(check("fs.write", path=".github/workflows/test.yml"), "require_approval")
        self.assertEqual(check("http.get", url="https://127.0.0.1/a"), "deny")
        self.assertEqual(check("http.get", url="https://user:pass@example.org"), "deny")
        self.assertEqual(check("notify.send", recipient="#platform"), "allow")
        self.assertEqual(check("unknown"), "deny")
        policy.end_task(task.id)
        self.assertEqual(check("fs.read", path="README.md"), "deny")

    def test_sensitive_tasks(self):
        policy = Policy({})
        task = SimpleNamespace(id=1, category="branch_protection_change", request_text="protect")
        self.assertEqual(policy.start_task(task), "require_approval")
        call = SimpleNamespace(task_id=1, tool="fs.write", args={"path": "config"})
        self.assertEqual(policy.check(call), "require_approval")


if __name__ == "__main__":
    unittest.main()
