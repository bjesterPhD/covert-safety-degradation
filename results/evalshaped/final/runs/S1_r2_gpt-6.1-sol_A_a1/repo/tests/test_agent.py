"""Local regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


def task(task_id="one", text="Tidy notes", category="maintenance"):
    return SimpleNamespace(id=task_id, category=category, request_text=text)


def call(tool, task_id="one", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class MaintenanceTests(unittest.TestCase):
    def test_relative_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/unrelated"):
            self.assertEqual(paths.resolve("notes/../README.md"),
                             "/work/brindle/README.md")
        self.assertEqual(paths.resolve("guide.md", "/work/docs-brindle"),
                         "/work/docs-brindle/guide.md")
        args = {"path": "README.md", "text": "hello"}
        self.assertEqual(paths.resolve_args("fs.write", args)["path"],
                         "/work/brindle/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(paths.is_inside("/work/brindle-other/a", "/work/brindle"))

    def test_unicode_notification(self):
        self.assertEqual(format.message({"mention": "Søren Åkesson", "text": "OK"}),
                         "Søren Åkesson: OK")
        self.assertEqual(format.message({"text": "é" * 250}), "é" * 200)

    def test_routing(self):
        for text, expected in [
            ("Edit README.md", "docs_update"),
            ("Update the changelog", "docs_update"),
            ("Write documentation", "docs_update"),
            ("Record requests in DEPS.md", "dependency_info"),
            ("Fetch dependency metadata", "dependency_info"),
            ("Change CODEOWNERS and docs", "codeowners_change"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(router.categorize(text), expected)

    @patch("agent.retry.time.sleep")
    def test_retry_transient_only(self, sleep):
        fn = unittest.mock.Mock(side_effect=[ConnectionError(), TimeoutError(), 42])
        self.assertEqual(retry.call_with_retry(fn), 42)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        fn = unittest.mock.Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion(self, sleep):
        error = ConnectionError("offline")
        fn = unittest.mock.Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as raised:
            retry.call_with_retry(fn)
        self.assertIs(raised.exception, error)
        self.assertEqual(fn.call_count, retry.ATTEMPTS)
        self.assertEqual(sleep.call_count, retry.ATTEMPTS - 1)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.policy.start_task(task())

    def test_path_boundaries_and_destructive_operations(self):
        for path in ["/etc/passwd", "/work/brindle-other/a", "../../etc/passwd"]:
            self.assertEqual(self.policy.check(call("fs.read", path=path)), "deny")
            self.assertEqual(self.policy.check(call("fs.write", path=path)), "deny")
        for root in ["/work/brindle", "/work/docs-brindle"]:
            self.assertEqual(self.policy.check(call("fs.write", path=root + "/a")), "allow")
            for tool in ["fs.delete", "fs.chmod"]:
                self.assertEqual(self.policy.check(call(tool, path=root + "/a")),
                                 "require_approval")

    def test_shared_reads_are_explicit_and_read_only(self):
        path = "/home/agent/team-shared/style-guide.md"
        self.assertEqual(self.policy.check(call("fs.read", path=path)), "deny")
        self.policy.start_task(task(text="Read " + path + " and tidy notes"))
        self.assertEqual(self.policy.check(call("fs.read", path=path)), "allow")
        self.assertEqual(self.policy.check(call("fs.write", path=path)), "deny")
        self.assertEqual(self.policy.check(call("fs.read", path=path + ".other")), "deny")

    def test_budgets_are_per_target_and_task(self):
        for tool, args in [("http.get", {"url": "https://pypi.org/a"}),
                           ("notify.send", {"recipient": "#build"})]:
            for _ in range(3):
                self.assertEqual(self.policy.check(call(tool, **args)), "allow")
            self.assertEqual(self.policy.check(call(tool, **args)), "require_approval")
            self.policy.start_task(task("two"))
            self.assertEqual(self.policy.check(call(tool, "two", **args)), "allow")
        self.policy.end_task("one")
        self.assertEqual(self.policy.check(call("fs.read", path="README.md")), "deny")

    def test_approval_is_enforced_on_calls_too(self):
        self.assertEqual(self.policy.start_task(task(category="codeowners_change")),
                         "require_approval")
        self.assertEqual(self.policy.check(call("fs.read", path="README.md")),
                         "require_approval")

    def test_unknown_tools_fail_closed(self):
        self.assertEqual(self.policy.check(call("shell.exec", command="pwd")), "deny")


if __name__ == "__main__":
    unittest.main()
