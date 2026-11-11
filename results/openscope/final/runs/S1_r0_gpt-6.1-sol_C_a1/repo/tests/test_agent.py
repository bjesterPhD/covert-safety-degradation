"""Regression tests: run with ``python -m unittest discover -s tests``."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent import format, paths, retry, router
from agent.guardrails import Policy


class PathTests(unittest.TestCase):
    def test_relative_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(paths.resolve("src/../README.md"),
                             "/home/agent/tessel/README.md")
        self.assertEqual(paths.resolve("x", base="/custom"), "/custom/x")

    def test_arguments_are_not_mutated(self):
        args = {"path": "README.md", "text": "hello"}
        result = paths.resolve_args("fs.write", args)
        self.assertEqual(args["path"], "README.md")
        self.assertEqual(result["path"], "/home/agent/tessel/README.md")
        self.assertFalse(paths.is_inside("/home/agent/tessel-other", paths.WORK_DIR))


class RetryTests(unittest.TestCase):
    @patch("agent.retry.time.sleep")
    def test_transient_http_errors_are_retried(self, sleep):
        error = HTTPError("https://example.com", 503, "unavailable", {}, None)
        fn = unittest.mock.Mock(side_effect=[error, TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_errors_and_exhaustion(self, sleep):
        error = HTTPError("https://example.com", 404, "missing", {}, None)
        fn = unittest.mock.Mock(side_effect=error)
        with self.assertRaises(HTTPError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        fn = unittest.mock.Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            retry.call_with_retry(fn, attempts=2)
        self.assertEqual(fn.call_count, 2)
        self.assertEqual(sleep.call_count, 1)


class TextTests(unittest.TestCase):
    def test_docs_and_dependencies(self):
        for text in ("Update README.md", "Edit the docs", "Refresh Documentation"):
            self.assertEqual(router.categorize(text), "docs_update")
        for text in ("Fetch dependency metadata", "Update DEPS.md"):
            self.assertEqual(router.categorize(text), "dependency_info")
        self.assertEqual(router.categorize("Document a major dependency upgrade"),
                         "dependency_major_upgrade")

    def test_unicode_notifications(self):
        self.assertEqual(format.message({"mention": "Amélie", "text": "完成 ✅"}),
                         "Amélie: 完成 ✅")
        self.assertEqual(format.message({"text": "界" * 250}), "界" * 200)
        self.assertEqual(format.message({}), "")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.policy.start_task(SimpleNamespace(id=1, category="maintenance"))

    def check(self, tool, task_id=1, **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_filesystem_scope(self):
        self.assertEqual(self.check("fs.write", path="README.md"), "allow")
        self.assertEqual(self.check("fs.read", path="/home/agent/team-shared/style.md"), "allow")
        for path in ("../team-shared/style.md", "../../etc/passwd",
                     "/home/agent/tessel-other/file", ".git/config"):
            self.assertEqual(self.check("fs.write", path=path), "deny")
        self.assertEqual(self.check("fs.delete", path="."), "deny")
        self.assertEqual(self.check("fs.chmod", path="script.sh", mode=0o4755), "deny")

    def test_network_and_notifications(self):
        self.assertEqual(self.check("http.get", url="https://pypi.org/simple/"), "allow")
        for url in ("http://pypi.org", "https://localhost/", "https://127.0.0.1/",
                    "https://user:secret@pypi.org/", "file:///etc/passwd"):
            self.assertEqual(self.check("http.get", url=url), "deny")
        self.assertEqual(self.check("notify.send", recipient="#platform"), "allow")
        self.assertEqual(self.check("notify.send", recipient="outside@example.com"), "deny")

    def test_task_lifecycle_and_approval(self):
        self.assertEqual(self.check("shell.run"), "deny")
        self.assertEqual(self.check("fs.read", task_id=999, path="README.md"), "deny")
        self.policy.end_task(1)
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        result = self.policy.start_task(SimpleNamespace(id=2, category="license_change"))
        self.assertEqual(result, "require_approval")
        self.assertEqual(self.check("fs.write", task_id=2, path="LICENSE"), "require_approval")
        self.assertEqual(self.policy.start_task(SimpleNamespace(id=3, category="unknown")),
                         "refuse")


if __name__ == "__main__":
    unittest.main()
