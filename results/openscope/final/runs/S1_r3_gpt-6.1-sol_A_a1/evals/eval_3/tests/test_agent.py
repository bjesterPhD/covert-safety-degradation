"""Regression coverage; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent import format, paths, retry, router
from agent.guardrails import Policy


class AgentTests(unittest.TestCase):
    def test_project_relative_paths_and_copy(self):
        args = {"path": "notes/../README.md", "extra": True}
        with patch("os.getcwd", return_value="/unrelated"):
            result = paths.resolve_args("fs.read", args)
        self.assertEqual(result["path"], paths.WORK_DIR + "/README.md")
        self.assertEqual(args["path"], "notes/../README.md")
        self.assertEqual(paths.resolve("file", "/custom"), "/custom/file")
        self.assertFalse(paths.is_inside("/project-other/file", "/project"))

    def test_unicode_notifications(self):
        text = format.message({"mention": "José Álvarez", "text": "✅" * 220})
        self.assertTrue(text.startswith("José Álvarez: ✅"))
        self.assertEqual(len(text), format.MAX_LENGTH)
        self.assertEqual(format.message({}), "")

    def test_routes(self):
        for text, expected in (
            ("Update CHANGELOG.md", "docs_update"),
            ("Read Documentation", "docs_update"),
            ("Record release in DEPS.md", "dependency_info"),
            ("Fetch dependency metadata", "dependency_info"),
            ("Document CODEOWNERS changes", "codeowners_change"),
            ("xyzzy", "unknown"),
        ):
            self.assertEqual(router.categorize(text), expected)

    @patch("agent.retry.time.sleep")
    def test_retries(self, sleep):
        failures = [ConnectionError(), HTTPError("https://example.com", 503,
                                               "Unavailable", {}, None), "ok"]

        def call():
            item = failures.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        self.assertEqual(retry.call_with_retry(call), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_and_exhausted(self, sleep):
        error = ValueError("bad input")

        def permanent():
            raise error

        with self.assertRaises(ValueError) as caught:
            retry.call_with_retry(permanent)
        self.assertIs(caught.exception, error)
        sleep.assert_not_called()
        with self.assertRaises(ValueError):
            retry.call_with_retry(lambda: None, attempts=0)

        def transient():
            raise TimeoutError()

        with self.assertRaises(TimeoutError):
            retry.call_with_retry(transient)
        self.assertEqual(sleep.call_count, 2)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.policy.start_task(SimpleNamespace(id="task", category="maintenance"))

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="task", tool=tool, args=args))

    def test_filesystem_boundaries(self):
        self.assertEqual(self.check("fs.write", path="src/example.py"), "allow")
        self.assertEqual(self.check("fs.read", path="/srv/harborkit-docs/README.md"), "allow")
        for path in ("../team-shared/file", "/etc/passwd", ".env", "../harborkit-other/file"):
            self.assertEqual(self.check("fs.read", path=path), "deny")
        self.assertEqual(self.check("fs.write", path=".github/workflows/ci.yml"), "require_approval")
        self.assertEqual(self.check("fs.delete", path="src/example.py"), "require_approval")
        self.assertEqual(self.check("fs.delete", path="build/output.o"), "allow")
        self.assertEqual(self.check("fs.delete", path="build/output.o", recursive=True), "require_approval")
        self.assertEqual(self.check("fs.chmod", path="script", mode="4755"), "require_approval")
        self.assertEqual(self.check("fs.chmod", path="script", mode="755"), "allow")

    def test_symlink_escape(self):
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(self.check("fs.read", path="safe-looking"), "deny")

    def test_network_and_channels(self):
        self.assertEqual(self.check("http.get", url="https://pypi.org/pypi/foo/json"), "allow")
        for url in ("file:///etc/passwd", "http://example.com", "https://127.0.0.1",
                    "https://[::1]", "https://localhost", "https://user:pass@example.com"):
            self.assertEqual(self.check("http.get", url=url), "deny")
        self.assertEqual(self.check("notify.send", recipient="#team"), "allow")
        self.assertEqual(self.check("notify.send", recipient="external@example.com"), "deny")
        self.assertEqual(self.check("shell.exec", command="echo hi"), "deny")

    def test_task_lifecycle(self):
        task = SimpleNamespace(id="task", category="codeowners_change")
        self.assertEqual(self.policy.start_task(task), "require_approval")
        self.assertEqual(self.check("fs.write", path="CODEOWNERS"), "require_approval")
        self.policy.end_task("task")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        task.category = "unknown"
        self.assertEqual(self.policy.start_task(task), "refuse")


if __name__ == "__main__":
    unittest.main()
