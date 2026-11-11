"""Regression tests: python -m unittest discover -s tests."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import is_inside, resolve, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class UtilityTests(unittest.TestCase):
    def test_relative_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/unrelated"):
            self.assertEqual(resolve("notes/../README.md"), "/srv/quillpath/README.md")
            self.assertEqual(resolve("guide.md", "/docs"), "/docs/guide.md")
        args = {"path": "README.md", "text": "unchanged"}
        self.assertEqual(resolve_args("fs.read", args)["path"], "/srv/quillpath/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertIs(resolve_args("notify.send", args), args)

    def test_path_boundaries(self):
        self.assertTrue(is_inside("/srv/quillpath/a", "/srv/quillpath"))
        self.assertFalse(is_inside("/srv/quillpath-other/a", "/srv/quillpath"))
        self.assertFalse(is_inside("/srv/quillpath/../common/a", "/srv/quillpath"))

    def test_routing(self):
        for text, expected in [
            ("Update README.md", "docs_update"),
            ("Update the changelog", "docs_update"),
            ("Record release in DEPS.md", "dependency_info"),
            ("Fetch dependency metadata", "dependency_info"),
            ("Change CODEOWNERS in the docs", "codeowners_change"),
            ("Notify the docs team", "notification"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(categorize(text), expected)

    def test_unicode_notifications(self):
        self.assertEqual(message({"mention": "Amélie", "text": "Ready ✓"}), "Amélie: Ready ✓")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "界" * 300}), "界" * MAX_LENGTH)

    @patch("agent.retry.time.sleep")
    def test_transient_retry_and_backoff(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_failure_is_not_retried(self, sleep):
        fn = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()

    @patch("agent.retry.time.sleep")
    def test_exhaustion_and_attempt_validation(self, sleep):
        fn = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)

    @patch("agent.retry.time.sleep")
    def test_http_status_retry(self, sleep):
        error = RuntimeError("service unavailable")
        error.response = SimpleNamespace(status_code=503)
        fn = Mock(side_effect=[error, "ok"])
        self.assertEqual(call_with_retry(fn), "ok")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.task = SimpleNamespace(id="one", category="maintenance", request_text="Tidy notes")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

    def test_filesystem_scope_and_approval(self):
        for root in ("/srv/quillpath", "/home/agent/docs/quillpath"):
            self.assertEqual(self.check("fs.read", path=root + "/notes.txt"), "allow")
            self.assertEqual(self.check("fs.write", path=root + "/notes.txt"), "allow")
            self.assertEqual(self.check("fs.delete", path=root + "/notes.txt"), "require_approval")
            self.assertEqual(self.check("fs.chmod", path=root + "/script", mode="755"), "require_approval")
        for path in ("/etc/passwd", "../common/file", "/srv/quillpath-other/file", "bad\x00path"):
            self.assertEqual(self.check("fs.read", path=path), "deny")
            self.assertEqual(self.check("fs.write", path=path), "deny")

    def test_symlink_escape(self):
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(self.check("fs.read", path="notes/link"), "deny")

    def test_fail_closed(self):
        self.assertEqual(self.check("shell.exec", command="ls"), "deny")
        self.assertEqual(self.check("fs.read"), "deny")
        self.assertEqual(self.check("http.get", url="file:///etc/passwd"), "deny")
        self.assertEqual(self.check("notify.send", recipient="someone@example.com"), "deny")
        self.policy.end_task("one")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        self.policy.end_task("one")  # Cleanup is idempotent.

    def test_sensitive_task_stays_blocked(self):
        self.task.category = "codeowners_change"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("fs.read", path="README.md"), "require_approval")

    def test_task_budgets_and_cleanup(self):
        for i in range(5):
            self.assertEqual(self.check("http.get", url=f"https://host-{i}.example/"), "allow")
            self.assertEqual(self.check("notify.send", recipient=f"#team-{i}"), "allow")
        self.assertEqual(self.check("http.get", url="https://new.example/"), "require_approval")
        self.assertEqual(self.check("notify.send", recipient="#new"), "require_approval")
        self.policy.end_task("one")
        self.policy.start_task(self.task)
        self.assertEqual(self.check("http.get", url="https://new.example/"), "allow")
        self.assertEqual(self.check("notify.send", recipient="#new"), "allow")


if __name__ == "__main__":
    unittest.main()
