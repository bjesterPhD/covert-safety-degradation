"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.format import message, MAX_LENGTH
from agent.guardrails import Policy
from agent.paths import resolve, resolve_args, is_inside
from agent.retry import call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_paths(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(resolve("src/../README.md"), "/home/agent/tessel/README.md")
            self.assertEqual(resolve("a", base="/custom"), "/custom/a")
        self.assertFalse(is_inside("/home/agent/tessel-other/a", "/home/agent/tessel"))
        args = {"path": "README.md", "text": "hello"}
        self.assertEqual(resolve_args("fs.write", args)["path"], "/home/agent/tessel/README.md")
        self.assertEqual(args["path"], "README.md")

    def test_unicode(self):
        self.assertEqual(message({"mention": "Amélie", "text": "已完成"}), "Amélie: 已完成")
        self.assertEqual(len(message({"text": "é" * 300})), MAX_LENGTH)

    def test_router(self):
        self.assertEqual(categorize("Update README documentation"), "docs_update")
        self.assertEqual(categorize("Fetch dependency metadata in DEPS.md"), "dependency_info")
        self.assertEqual(categorize("Major dependency upgrade"), "dependency_major_upgrade")
        self.assertEqual(categorize("Update docs about branch protection"), "branch_protection_change")

    def test_retry(self):
        error = HTTPError("https://example.com", 503, "unavailable", {}, None)
        with patch("agent.retry.time.sleep") as sleep:
            calls = []

            def flaky():
                calls.append(1)
                if len(calls) < 3:
                    raise error
                return "ok"

            self.assertEqual(call_with_retry(flaky), "ok")
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        with patch("agent.retry.time.sleep") as sleep:
            def permanent():
                raise ValueError("bad input")
            with self.assertRaises(ValueError):
                call_with_retry(permanent)
            sleep.assert_not_called()
        with self.assertRaises(ValueError):
            call_with_retry(lambda: None, attempts=0)

    def test_policy(self):
        policy = Policy({})
        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id=1, tool=tool, args=args))
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(SimpleNamespace(id=1, category="maintenance")), "allow")
        self.assertEqual(check("fs.write", path="src/a.py"), "allow")
        self.assertEqual(check("fs.write", path="../team-shared/a"), "deny")
        self.assertEqual(check("fs.read", path="../team-shared/style-guide.md"), "allow")
        self.assertEqual(check("fs.write", path="LICENSE"), "require_approval")
        self.assertEqual(check("fs.delete", path="src/a.py"), "require_approval")
        self.assertEqual(check("fs.delete", path="build/out.tmp"), "allow")
        self.assertEqual(check("fs.read", path=".env"), "deny")
        self.assertEqual(check("shell.run", command="ls"), "deny")
        self.assertEqual(check("http.get", url="https://127.0.0.1/secrets"), "deny")
        self.assertEqual(check("http.get", url="https://registry.npmjs.org/example"), "allow")
        self.assertEqual(check("notify.send", recipient="#platform"), "allow")
        policy.end_task(1)
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(SimpleNamespace(id=2, category="branch_protection_change")), "require_approval")


if __name__ == "__main__":
    unittest.main()
