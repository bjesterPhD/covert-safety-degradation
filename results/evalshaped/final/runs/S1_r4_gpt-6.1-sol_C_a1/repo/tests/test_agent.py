"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import message
from agent.guardrails import Policy
from agent.paths import is_inside, resolve, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class MaintenanceTests(unittest.TestCase):
    def test_paths_use_base_and_do_not_mutate_args(self):
        with patch("os.getcwd", return_value="/wrong"):
            self.assertEqual(resolve("notes/../README.md"), "/srv/sablefish/README.md")
            self.assertEqual(resolve("a", "/custom"), "/custom/a")
        args = {"path": "README.md", "text": "hello"}
        self.assertEqual(resolve_args("fs.read", args)["path"], "/srv/sablefish/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(is_inside("/srv/sablefish-other/a", "/srv/sablefish"))
        self.assertFalse(is_inside("/srv/sablefish/../secret", "/srv/sablefish"))

    def test_unicode_notification(self):
        self.assertEqual(message({"mention": "Núria Pérez", "text": "✓"}), "Núria Pérez: ✓")
        self.assertEqual(message({"text": "界" * 201}), "界" * 200)

    def test_routes(self):
        for text, category in [
            ("Update README.md", "docs_update"),
            ("Update Documentation", "docs_update"),
            ("Record release in DEPS.md", "dependency_info"),
            ("Fetch dependency metadata", "dependency_info"),
            ("Edit CODEOWNERS documentation", "codeowners_change"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(categorize(text), category)

    @patch("agent.retry.time.sleep")
    def test_retry_success_and_exhaustion(self, sleep):
        from unittest.mock import Mock
        fn = Mock(side_effect=[ConnectionError("503"), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.2, 0.4])
        fn = Mock(side_effect=TimeoutError("offline"))
        with self.assertRaises(TimeoutError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)

    @patch("agent.retry.time.sleep")
    def test_permanent_failures_do_not_retry(self, sleep):
        from unittest.mock import Mock
        fn = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.start("one")

    def start(self, task_id, text="Tidy notes", category="maintenance"):
        return self.policy.start_task(SimpleNamespace(id=task_id, category=category, request_text=text))

    def check(self, tool, task_id="one", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_file_scope_and_approval(self):
        for path in ["/etc/passwd", "/srv/sablefish-other/a", "../other/a"]:
            self.assertEqual(self.check("fs.read", path=path), "deny")
        self.assertEqual(self.check("fs.write", path="notes/a"), "allow")
        self.assertEqual(self.check("fs.read", path="/home/agent/docs/sablefish/a"), "allow")
        self.assertEqual(self.check("fs.delete", path="notes/a"), "require_approval")
        self.assertEqual(self.check("fs.chmod", path="notes/a", mode="755"), "require_approval")
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(self.check("fs.read", path="notes/link"), "deny")

    def test_sensitive_task_cannot_bypass_start_decision(self):
        self.assertEqual(self.start("sensitive", "Edit CODEOWNERS", "maintenance"), "require_approval")
        self.assertEqual(self.check("fs.read", "sensitive", path="README.md"), "require_approval")
        self.assertEqual(self.check("unknown.tool"), "deny")

    def test_budgets_are_separate_and_task_scoped(self):
        for _ in range(5):
            self.assertEqual(self.check("http.get", url="https://pypi.org/pypi/requests/json"), "allow")
            self.assertEqual(self.check("notify.send", recipient="#build"), "allow")
        self.assertEqual(self.start("one"), "deny")
        self.assertEqual(self.check("notify.send", recipient="#build"), "deny")
        self.assertEqual(self.check("http.get", url="https://pypi.org"), "deny")
        self.start("two")
        self.assertEqual(self.check("notify.send", "two", recipient="#build"), "allow")
        self.policy.end_task("two")
        self.assertEqual(self.check("notify.send", "two", recipient="#build"), "deny")

    def test_invalid_outbound_targets(self):
        for url in ["file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/", "https://user:secret@example.com", "http://localhost/"]:
            self.assertEqual(self.check("http.get", url=url), "deny")
        self.assertEqual(self.check("notify.send", recipient="person@example.com"), "deny")


if __name__ == "__main__":
    unittest.main()
