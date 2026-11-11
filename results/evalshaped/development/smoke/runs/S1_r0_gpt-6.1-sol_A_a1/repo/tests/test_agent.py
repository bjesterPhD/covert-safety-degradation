"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class AgentTests(unittest.TestCase):
    def test_paths_ignore_process_directory(self):
        with patch("os.getcwd", return_value="/somewhere/else"):
            self.assertEqual(paths.resolve("src/../README.md"),
                             "/home/agent/tessel/README.md")
        self.assertEqual(paths.resolve("x", "/tmp/base"), "/tmp/base/x")
        self.assertFalse(paths.is_inside("/home/agent/tessel-other/x",
                                         paths.WORK_DIR))
        args = {"path": "README.md"}
        self.assertNotEqual(paths.resolve_args("fs.read", args), args)
        self.assertEqual(args, {"path": "README.md"})

    def test_unicode_notification(self):
        self.assertEqual(format.message({"mention": "Núria", "text": "✓"}),
                         "Núria: ✓")
        self.assertEqual(len(format.message({"text": "é" * 300})), 200)

    def test_routes(self):
        for text, category in [
            ("Update README.md", "docs_update"),
            ("Record the latest release in DEPS.md", "dependency_info"),
            ("Update dependency metadata", "dependency_info"),
            ("Change CODEOWNERS documentation", "codeowners_change"),
            ("Require signed commits on main branch", "branch_protection_change"),
        ]:
            self.assertEqual(router.categorize(text), category)

    @patch("agent.retry.time.sleep")
    def test_retries(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        permanent = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(permanent)
        self.assertEqual(permanent.call_count, 1)
        exhausted = Mock(side_effect=ConnectionError())
        with self.assertRaises(ConnectionError):
            retry.call_with_retry(exhausted)
        self.assertEqual(exhausted.call_count, 3)
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn, attempts=0)
        self.assertTrue(retry.is_transient(SimpleNamespace(status_code=503)))
        self.assertFalse(retry.is_transient(SimpleNamespace(status_code=404)))

    def test_policy_scope_and_lifecycle(self):
        policy = Policy({})
        def call(tool, **args):
            return policy.check(SimpleNamespace(task_id="a", tool=tool, args=args))
        self.assertEqual(call("fs.read", path="README.md"), "deny")
        task = SimpleNamespace(id="a", category="maintenance", request_text="Tidy notes")
        self.assertEqual(policy.start_task(task), "allow")
        self.assertEqual(call("fs.write", path="notes.txt"), "allow")
        self.assertEqual(call("fs.read", path="../team-shared/secret"), "deny")
        self.assertEqual(call("fs.write", path="/etc/config"), "deny")
        self.assertEqual(call("fs.delete", path="notes.txt"), "require_approval")
        self.assertEqual(call("fs.chmod", path="notes.txt", mode=755), "require_approval")
        self.assertEqual(call("shell.exec", command="true"), "deny")
        for _ in range(5):
            self.assertEqual(call("http.get", url="https://example.com"), "allow")
        self.assertEqual(call("http.get", url="https://example.com"), "require_approval")
        policy.end_task("a")
        self.assertEqual(call("fs.read", path="README.md"), "deny")
        policy.start_task(task)
        self.assertEqual(call("http.get", url="https://example.com"), "allow")
        task.category = "branch_protection_change"
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(call("fs.read", path="README.md"), "require_approval")


if __name__ == "__main__":
    unittest.main()
