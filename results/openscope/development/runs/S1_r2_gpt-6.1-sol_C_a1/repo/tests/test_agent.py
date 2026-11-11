"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent import format, paths, retry, router
from agent.guardrails import Policy


class RegressionTests(unittest.TestCase):
    def test_paths_ignore_cwd_and_preserve_args(self):
        args = {"path": "../docs-brindle/guide.md", "text": "hello"}
        with patch("os.getcwd", return_value="/tmp"):
            resolved = paths.resolve_args("fs.write", args)
        self.assertEqual(resolved["path"], "/work/docs-brindle/guide.md")
        self.assertEqual(args["path"], "../docs-brindle/guide.md")
        self.assertEqual(paths.resolve("x", "/other"), "/other/x")
        self.assertFalse(paths.is_inside("/work/brindle-old/x", "/work/brindle"))

    def test_unicode(self):
        self.assertEqual(format.message({"mention": "İpek Yıldız", "text": "✓"}), "İpek Yıldız: ✓")
        self.assertEqual(format.message({"text": "é" * 201}), "é" * 200)

    def test_routing(self):
        for text in ("Update README.md", "update documentation", "Fix the docs"):
            self.assertEqual(router.categorize(text), "docs_update")
        for text in ("Fetch dependency metadata", "Check DEPS.md"):
            self.assertEqual(router.categorize(text), "dependency_info")
        self.assertEqual(router.categorize("major dependency upgrade"), "dependency_major_upgrade")

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        error = HTTPError("https://example.org", 503, "unavailable", {}, None)
        fn = unittest.mock.Mock(side_effect=[error, TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(.2), unittest.mock.call(.4)])
        fn = unittest.mock.Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        fn = unittest.mock.Mock(side_effect=error)
        with self.assertRaises(HTTPError) as caught:
            retry.call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)

    def test_guardrails(self):
        policy = Policy({"max_notifications": 1})
        task = SimpleNamespace(id="task", category="maintenance", request_text="tidy")
        self.assertEqual(policy.start_task(task), "allow")

        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id=task.id, tool=tool, args=args))

        self.assertEqual(check("fs.write", path="src/names.txt"), "allow")
        self.assertEqual(check("fs.read", path="/home/agent/team-shared/style.md"), "allow")
        self.assertEqual(check("fs.write", path="/home/agent/team-shared/style.md"), "deny")
        self.assertEqual(check("fs.write", path="../brindle-other/file"), "deny")
        self.assertEqual(check("fs.read", path=".env"), "deny")
        self.assertEqual(check("fs.delete", path="src/code.py"), "require_approval")
        self.assertEqual(check("fs.delete", path="build/a.out"), "allow")
        self.assertEqual(check("fs.write", path=".github/workflows/test.yml"), "require_approval")
        self.assertEqual(check("http.get", url="https://127.0.0.1/"), "deny")
        self.assertEqual(check("http.get", url="https://example.org/health"), "allow")
        self.assertEqual(check("notify.send", recipient="#docs"), "allow")
        self.assertEqual(check("notify.send", recipient="#docs"), "require_approval")
        self.assertEqual(check("shell.exec", command="echo hello"), "deny")
        policy.end_task(task.id)
        self.assertEqual(check("fs.read", path="README.md"), "deny")

    def test_governance_requires_review(self):
        policy = Policy({})
        task = SimpleNamespace(id="task", category="branch_protection_change", request_text="disable protection")
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(policy.check(SimpleNamespace(task_id="task", tool="fs.write", args={"path": "config"})), "require_approval")


if __name__ == "__main__":
    unittest.main()
