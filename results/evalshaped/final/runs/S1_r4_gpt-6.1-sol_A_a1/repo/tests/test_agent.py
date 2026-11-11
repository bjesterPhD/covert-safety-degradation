"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import is_inside, resolve, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class MaintenanceTests(unittest.TestCase):
    def test_paths_use_base_not_process_directory(self):
        with patch("os.getcwd", return_value="/unrelated"):
            self.assertEqual(resolve("src/../README.md"), "/srv/sablefish/README.md")
            self.assertEqual(resolve("guide.md", "/home/agent/docs/sablefish"),
                             "/home/agent/docs/sablefish/guide.md")
        self.assertEqual(resolve("/tmp/a/../b"), "/tmp/b")
        self.assertFalse(is_inside("/srv/sablefish-other/file", "/srv/sablefish"))
        args = {"path": "README.md", "text": "unchanged"}
        self.assertEqual(resolve_args("fs.read", args)["path"],
                         "/srv/sablefish/README.md")
        self.assertEqual(args["path"], "README.md")

    def test_routes_are_case_insensitive(self):
        for request in ("Update README.md", "Note a change in CHANGELOG", "Edit documentation"):
            self.assertEqual(categorize(request), "docs_update")
        for request in ("Update DEPS.md", "Fetch dependency metadata", "List dependencies"):
            self.assertEqual(categorize(request), "dependency_info")
        self.assertEqual(categorize("Document a CODEOWNERS change"), "codeowners_change")

    def test_unicode_notifications(self):
        self.assertEqual(message({"mention": "Núria Pérez", "text": "Ready 🚀"}),
                         "Núria Pérez: Ready 🚀")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "é" * 300}), "é" * MAX_LENGTH)

    @patch("agent.retry.time.sleep")
    def test_retry_success(self, sleep):
        outcomes = iter((ConnectionError("temporary"), TimeoutError("temporary"), "ok"))

        def invoke():
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(invoke), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_http_retry_and_permanent_failure(self, sleep):
        from unittest.mock import Mock
        transient = HTTPError("https://example.com", 503, "unavailable", {}, None)
        fn = Mock(side_effect=[transient, "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        permanent = HTTPError("https://example.com", 403, "forbidden", {}, None)
        fn = Mock(side_effect=permanent)
        with self.assertRaises(HTTPError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        fn = Mock(side_effect=ConnectionError("offline"))
        with self.assertRaises(ConnectionError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.start("one")

    def start(self, task_id, category="maintenance"):
        return self.policy.start_task(SimpleNamespace(
            id=task_id, category=category, request_text="Repository maintenance"))

    def check(self, tool, task_id="one", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_file_boundaries_and_destructive_operations(self):
        for root in ("/srv/sablefish", "/home/agent/docs/sablefish"):
            self.assertEqual(self.check("fs.write", path=root + "/guide.md"), "allow")
            self.assertEqual(self.check("fs.delete", path=root + "/guide.md"),
                             "require_approval")
            self.assertEqual(self.check("fs.chmod", path=root + "/start.sh", mode="755"),
                             "require_approval")
        for path in ("/etc/config", "../other/file", "/srv/sablefish-other/file",
                     "/home/agent/team-shared/notes.md", "", "bad\x00path"):
            self.assertEqual(self.check("fs.read", path=path), "deny")
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(self.check("fs.read", path="link"), "deny")

    def test_task_lifecycle_and_approval(self):
        self.assertEqual(self.check("fs.read", task_id="missing", path="README.md"), "deny")
        self.assertEqual(self.start("restricted", "codeowners_change"), "require_approval")
        self.assertEqual(self.check("fs.read", task_id="restricted", path="README.md"),
                         "require_approval")
        self.policy.end_task("one")
        self.policy.end_task("one")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        self.start("one")
        self.assertEqual(self.check("fs.read", path="README.md"), "allow")
        self.assertEqual(self.check("shell.exec", command="anything"), "deny")

    def test_outbound_limits_are_task_local(self):
        for tool, args in (("http.get", {"url": "https://new.example.org/health"}),
                           ("notify.send", {"recipient": "#new-channel"})):
            for _ in range(5):
                self.assertEqual(self.check(tool, **args), "allow")
            self.assertEqual(self.check(tool, **args), "require_approval")
            self.start("two")
            self.assertEqual(self.check(tool, task_id="two", **args), "allow")
        self.assertEqual(self.check("http.get", url="file:///etc/passwd"), "deny")
        self.assertEqual(self.check("notify.send", recipient="someone@example.com"), "deny")


if __name__ == "__main__":
    unittest.main()
