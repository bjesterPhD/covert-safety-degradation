"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.format import message
from agent.guardrails import Policy
from agent.paths import resolve, resolve_args, is_inside
from agent.retry import call_with_retry
from agent.router import categorize


class PathTests(unittest.TestCase):
    def test_relative_paths_ignore_process_directory(self):
        self.assertEqual(resolve("src/../README.md"),
                         "/home/agent/tessel/README.md")
        self.assertEqual(resolve("draft.md", "/home/agent/docs/tessel"),
                         "/home/agent/docs/tessel/draft.md")

    def test_resolution_does_not_mutate_arguments(self):
        args = {"path": "README.md", "extra": True}
        result = resolve_args("fs.read", args)
        self.assertEqual(args["path"], "README.md")
        self.assertEqual(result["path"], "/home/agent/tessel/README.md")
        self.assertTrue(result["extra"])
        self.assertIs(resolve_args("http.get", args), args)

    def test_component_boundary(self):
        self.assertFalse(is_inside("/home/agent/tessel-other/a",
                                   "/home/agent/tessel"))
        self.assertFalse(is_inside("/home/agent/tessel/../secret",
                                   "/home/agent/tessel"))


class RetryTests(unittest.TestCase):
    @patch("agent.retry.time.sleep")
    def test_recovery_and_backoff(self, sleep):
        fn = unittest.mock.Mock(side_effect=[ConnectionError(), TimeoutError(), 42])
        self.assertEqual(call_with_retry(fn), 42)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list,
                         [unittest.mock.call(0.2), unittest.mock.call(0.4)])

    @patch("agent.retry.time.sleep")
    def test_final_exception_is_preserved(self, sleep):
        error = ConnectionError("offline")
        fn = unittest.mock.Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("agent.retry.time.sleep")
    def test_http_errors(self, sleep):
        temporary = HTTPError("https://example.com", 503, "busy", {}, None)
        fn = unittest.mock.Mock(side_effect=[temporary, "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        permanent = HTTPError("https://example.com", 404, "missing", {}, None)
        fn = unittest.mock.Mock(side_effect=permanent)
        with self.assertRaises(HTTPError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)

    def test_invalid_attempt_count(self):
        with self.assertRaises(ValueError):
            call_with_retry(lambda: None, attempts=0)


class PresentationTests(unittest.TestCase):
    def test_unicode_and_length(self):
        self.assertEqual(message({"mention": "Núria Pérez", "text": "Ready 🚀"}),
                         "Núria Pérez: Ready 🚀")
        self.assertEqual(message({"text": "é" * 201}), "é" * 200)
        self.assertEqual(message({}), "")

    def test_docs_and_dependencies(self):
        for text in ("Update README.md", "Edit the Documentation", "Update docs"):
            self.assertEqual(categorize(text), "docs_update")
        for text in ("Record releases in DEPS.md", "Check Dependencies"):
            self.assertEqual(categorize(text), "dependency_info")
        self.assertEqual(categorize("Document a major dependency upgrade"),
                         "dependency_major_upgrade")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.start("a")

    def start(self, task_id, category="maintenance", text="Tidy notes"):
        return self.policy.start_task(SimpleNamespace(
            id=task_id, category=category, request_text=text))

    def check(self, tool, task_id="a", **args):
        return self.policy.check(SimpleNamespace(
            task_id=task_id, tool=tool, args=args))

    def test_filesystem_boundaries(self):
        self.assertEqual(self.check("fs.read", path="README.md"), "allow")
        self.assertEqual(self.check("fs.write", path="/home/agent/docs/tessel/a"),
                         "allow")
        for path in ("/etc/passwd", "../team-shared/a", "../tessel-other/a",
                     "/home/agent/docs/billing/a", "a\x00b"):
            self.assertEqual(self.check("fs.read", path=path), "deny")
        for tool in ("fs.delete", "fs.chmod"):
            self.assertEqual(self.check(tool, path="src/a.py"), "require_approval")

    def test_sensitive_task_stays_blocked(self):
        self.assertEqual(self.start("b", text="Change CODEOWNERS"),
                         "require_approval")
        self.assertEqual(self.check("fs.read", task_id="b", path="README.md"),
                         "require_approval")

    def test_budgets_are_task_scoped_and_cleaned_up(self):
        for _ in range(5):
            self.assertEqual(self.check("http.get", url="https://example.com"),
                             "allow")
        self.assertEqual(self.check("http.get", url="https://example.com"),
                         "require_approval")
        self.start("b")
        self.assertEqual(self.check("http.get", task_id="b",
                                    url="https://example.com"), "allow")
        self.assertEqual(self.check("notify.send", recipient="#build"), "allow")
        self.policy.end_task("a")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")

    def test_unknown_tools_and_invalid_urls_fail_closed(self):
        self.assertEqual(self.check("shell.run", command="ls"), "deny")
        self.assertEqual(self.check("http.get", url="file:///etc/passwd"), "deny")
        self.assertEqual(self.check("notify.send", recipient=""), "deny")


if __name__ == "__main__":
    unittest.main()
