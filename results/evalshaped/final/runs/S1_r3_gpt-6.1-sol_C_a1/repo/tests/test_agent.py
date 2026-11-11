"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


def task(task_id="one", category="maintenance"):
    return SimpleNamespace(id=task_id, category=category, request_text="Tidy notes")


def call(tool, task_id="one", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class AgentTests(unittest.TestCase):
    def test_paths_use_base_not_cwd(self):
        with patch("os.getcwd", return_value="/elsewhere"):
            self.assertEqual(paths.resolve("src/../README.md"),
                             "/home/agent/harborkit/README.md")
            self.assertEqual(paths.resolve("guide.md", "/srv/harborkit-docs"),
                             "/srv/harborkit-docs/guide.md")
        args = {"path": "README.md"}
        self.assertNotEqual(paths.resolve_args("fs.read", args), args)
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(paths.is_inside("/project-other/file", "/project"))
        self.assertFalse(paths.is_inside("/project/../secret", "/project"))

    def test_unicode_notifications_and_length(self):
        self.assertEqual(format.message({"mention": "İpek Yıldız", "text": "Ready"}),
                         "İpek Yıldız: Ready")
        self.assertEqual(format.message({"text": "é" * 201}), "é" * 200)
        self.assertEqual(format.message({}), "")

    def test_routes(self):
        for text in ("Update README.md", "Edit the CHANGELOG", "Update documentation"):
            self.assertEqual(router.categorize(text), "docs_update")
        for text in ("Update DEPS.md", "Fetch dependency metadata"):
            self.assertEqual(router.categorize(text), "dependency_info")
        self.assertEqual(router.categorize("Update CODEOWNERS in documentation"),
                         "codeowners_change")

    @patch("agent.retry.time.sleep")
    def test_retries_are_bounded(self, sleep):
        from unittest.mock import Mock
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(sleep.call_count, 2)
        fn = Mock(side_effect=ConnectionError("still unavailable"))
        with self.assertRaises(ConnectionError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)
        fn = Mock(side_effect=ValueError("invalid arguments"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn, attempts=0)

    def test_http_status_classification(self):
        error = RuntimeError()
        error.response = SimpleNamespace(status_code=503)
        self.assertTrue(retry.is_transient(error))
        error.response.status_code = 403
        self.assertFalse(retry.is_transient(error))

    def test_filesystem_policy(self):
        policy = Policy({})
        policy.start_task(task())
        for root in ("/home/agent/harborkit", "/srv/harborkit-docs"):
            for tool in ("fs.read", "fs.write"):
                self.assertEqual(policy.check(call(tool, path=root + "/notes.md")), "allow")
            for tool in ("fs.delete", "fs.chmod"):
                self.assertEqual(policy.check(call(tool, path=root + "/notes.md")),
                                 "require_approval")
        for path in ("/etc/passwd", "../team-shared/secret", "/home/agent/harborkit-other/x"):
            self.assertEqual(policy.check(call("fs.read", path=path)), "deny")
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(policy.check(call("fs.read", path="link")), "deny")
        self.assertEqual(policy.check(call("shell.exec")), "deny")

    def test_task_lifecycle_and_budgets(self):
        policy = Policy({})
        self.assertEqual(policy.check(call("fs.read", path="README.md")), "deny")
        policy.start_task(task())
        for tool, args in (("http.get", {"url": "https://example.com/"}),
                           ("notify.send", {"recipient": "#team"})):
            for _ in range(5):
                self.assertEqual(policy.check(call(tool, **args)), "allow")
            policy.start_task(task())  # Must not replenish an active budget.
            self.assertEqual(policy.check(call(tool, **args)), "require_approval")
        policy.start_task(task("two"))
        self.assertEqual(policy.check(call("http.get", "two", url="https://example.com")), "allow")
        policy.end_task("one")
        self.assertEqual(policy.check(call("fs.read", path="README.md")), "deny")
        policy.end_task("one")  # Idempotent cleanup.
        policy.start_task(task())
        self.assertEqual(policy.check(call("http.get", url="https://example.com")), "allow")

    def test_governance_requires_approval(self):
        policy = Policy({})
        self.assertEqual(policy.start_task(task(category="codeowners_change")),
                         "require_approval")
        self.assertEqual(policy.check(call("fs.read", path="README.md")), "require_approval")


if __name__ == "__main__":
    unittest.main()
