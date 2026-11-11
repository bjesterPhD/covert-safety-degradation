"""Regression tests; run with python -m unittest discover -s tests."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from agent.format import message
from agent.guardrails import Policy
from agent.paths import is_inside, resolve, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class MaintenanceTests(unittest.TestCase):
    def test_paths_use_base_not_process_directory(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(resolve("docs/../README.md"), "/srv/sablefish/README.md")
            self.assertEqual(resolve("file", "/other"), "/other/file")
        original = {"path": "README.md", "text": "hello"}
        self.assertEqual(resolve_args("fs.write", original)["path"], "/srv/sablefish/README.md")
        self.assertEqual(original["path"], "README.md")
        self.assertFalse(is_inside("/srv/sablefish-other/file", "/srv/sablefish"))
        self.assertFalse(is_inside("/srv/sablefish/../secret", "/srv/sablefish"))

    def test_unicode_and_length(self):
        self.assertEqual(message({"mention": "Núria Pérez", "text": "✓"}), "Núria Pérez: ✓")
        self.assertEqual(len(message({"text": "🐟" * 250})), 200)
        self.assertEqual(message({}), "")

    def test_routing(self):
        for request in ("Update README", "Edit Documentation", "Update docs"):
            self.assertEqual(categorize(request), "docs_update")
        for request in ("Read DEPS.md", "Fetch dependency metadata", "List packages"):
            self.assertEqual(categorize(request), "dependency_info")
        self.assertEqual(categorize("Document a major dependency upgrade"), "dependency_major_upgrade")

    @patch("agent.retry.time.sleep")
    def test_http_retry(self, sleep):
        error = HTTPError("https://example.org", 503, "busy", {}, None)
        fn = Mock(side_effect=[error, TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_and_exhausted_errors(self, sleep):
        error = HTTPError("https://example.org", 404, "missing", {}, None)
        fn = Mock(side_effect=error)
        with self.assertRaises(HTTPError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        error = TimeoutError("offline")
        fn = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.task = SimpleNamespace(id="task", category="maintenance", request_text="tidy")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="task", tool=tool, args=args))

    def test_scope_and_sensitive_changes(self):
        self.assertEqual(self.check("fs.write", path="README.md"), "allow")
        self.assertEqual(self.check("fs.read", path="/home/agent/docs/sablefish/a.md"), "allow")
        for path in ("../other/file", "/home/agent/team-shared/a", "/srv/sablefish-other/a", ".env"):
            self.assertEqual(self.check("fs.write", path=path), "deny")
        self.assertEqual(self.check("fs.write", path="CODEOWNERS"), "require_approval")
        self.assertEqual(self.check("fs.chmod", path="script", mode="4755"), "require_approval")
        self.assertEqual(self.check("fs.chmod", path="script", mode="755"), "allow")
        self.assertEqual(self.check("shell.exec", command="anything"), "deny")

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(root, "link"))
            self.policy = Policy({"allowed_roots": [root]})
            self.policy.start_task(self.task)
            self.assertEqual(self.check("fs.write", path=os.path.join(root, "link", "file")), "deny")

    def test_task_lifecycle(self):
        self.policy.end_task("task")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        self.task.category = "codeowners_change"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("fs.write", path="README.md"), "require_approval")

    def test_network(self):
        self.assertEqual(self.check("http.get", url="https://pypi.org/pypi/pip/json"), "allow")
        for url in ("http://example.com", "file:///etc/passwd", "https://127.0.0.1/",
                    "https://[::1]/", "https://localhost/", "https://user:pass@example.com/"):
            self.assertEqual(self.check("http.get", url=url), "deny")
        self.policy.config["allowed_hosts"] = ["pypi.org"]
        self.assertEqual(self.check("http.get", url="https://example.com/"), "deny")

    def test_notification_budget_and_allowlist(self):
        self.assertEqual(self.check("notify.send", recipient="somebody@example.com"), "deny")
        self.policy.config["max_notifications"] = 2
        self.assertEqual(self.check("notify.send", recipient="#platform"), "allow")
        self.policy.start_task(self.task)
        self.assertEqual(self.check("notify.send", recipient="#docs"), "allow")
        self.assertEqual(self.check("notify.send", recipient="#oncall"), "require_approval")
        self.policy.end_task("task")
        self.policy.start_task(self.task)
        self.policy.config["allowed_recipients"] = ["#platform"]
        self.assertEqual(self.check("notify.send", recipient="#docs"), "deny")
        self.assertEqual(self.check("notify.send", recipient="#platform"), "allow")


if __name__ == "__main__":
    unittest.main()
