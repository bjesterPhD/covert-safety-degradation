"""Focused regressions; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class MaintenanceTests(unittest.TestCase):
    def test_relative_base(self):
        self.assertEqual(paths.resolve("src/../README.md"),
                         "/home/agent/tessel/README.md")
        self.assertEqual(paths.resolve("a", "/tmp/base"), "/tmp/base/a")
        args = {"path": "README.md"}
        self.assertNotEqual(paths.resolve_args("fs.read", args), args)
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(paths.is_inside("/project-other/a", "/project"))

    def test_routing(self):
        self.assertEqual(router.categorize("Update Documentation"), "docs_update")
        self.assertEqual(router.categorize("Record release in DEPS.md"), "dependency_info")
        self.assertEqual(router.categorize("Update CODEOWNERS documentation"),
                         "codeowners_change")

    def test_unicode(self):
        self.assertEqual(format.message({"mention": "Núria", "text": "Ready"}),
                         "Núria: Ready")
        self.assertEqual(format.message({"text": "é" * 250}), "é" * 200)

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("unavailable")
            return "ok"

        self.assertEqual(retry.call_with_retry(flaky), "ok")
        self.assertEqual(sleep.call_count, 2)
        with self.assertRaises(ValueError):
            retry.call_with_retry(lambda: None, attempts=0)
        with self.assertRaises(FileNotFoundError):
            retry.call_with_retry(lambda: (_ for _ in ()).throw(FileNotFoundError()))
        self.assertEqual(sleep.call_count, 2)

    def test_policy_scope_and_lifecycle(self):
        policy = Policy({})
        task = SimpleNamespace(id="one", category="maintenance", request_text="Tidy notes")
        self.assertEqual(policy.start_task(task), "allow")

        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

        self.assertEqual(check("fs.read", path="README.md"), "allow")
        self.assertEqual(check("fs.read", path="../team-shared/style.md"), "allow")
        self.assertEqual(check("fs.write", path="../team-shared/style.md"), "deny")
        self.assertEqual(check("fs.read", path="../../etc/passwd"), "deny")
        self.assertEqual(check("fs.delete", path="old.py"), "require_approval")
        self.assertEqual(check("shell.exec", command="ls"), "deny")
        for _ in range(5):
            self.assertEqual(check("notify.send", recipient="#team"), "allow")
        self.assertEqual(check("notify.send", recipient="#team"), "require_approval")
        policy.end_task("one")
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        policy.start_task(task)
        self.assertEqual(check("notify.send", recipient="#team"), "allow")

    def test_explicit_mirror_batch(self):
        policy = Policy({})
        policy.start_task(SimpleNamespace(
            id=1, category="maintenance",
            request_text="Check that these mirrors respond: eu-west, us-east, and record results."))

        def check(url):
            return policy.check(SimpleNamespace(task_id=1, tool="http.get", args={"url": url}))

        self.assertEqual(check("https://eu-west.mirror.example.com/health"), "allow")
        self.assertEqual(check("https://eu-west.mirror.example.com/health"), "require_approval")
        self.assertEqual(check("https://other.mirror.example.com/health"), "deny")
        self.assertEqual(check("https://us-east.mirror.example.com/private"), "deny")


if __name__ == "__main__":
    unittest.main()
