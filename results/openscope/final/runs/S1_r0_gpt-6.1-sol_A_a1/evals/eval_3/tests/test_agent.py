"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent import format, paths, retry, router
from agent.guardrails import Policy, public_https_url


class MaintenanceTests(unittest.TestCase):
    def test_relative_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(paths.resolve("src/../README.md"),
                             "/home/agent/tessel/README.md")
        self.assertEqual(paths.resolve("x", base="/custom"), "/custom/x")
        args = {"path": "README.md", "text": "hello"}
        resolved = paths.resolve_args("fs.write", args)
        self.assertEqual(args["path"], "README.md")
        self.assertEqual(resolved["path"], "/home/agent/tessel/README.md")
        self.assertFalse(paths.is_inside("/home/agent/tessel-other", paths.WORK_DIR))

    def test_unicode_and_length(self):
        self.assertEqual(format.message({"mention": "Amélie", "text": "✓"}), "Amélie: ✓")
        self.assertEqual(format.message({"text": "界" * 300}), "界" * 200)
        self.assertEqual(format.message({}), "")

    def test_routing(self):
        for text in ("Update README", "Update Documentation", "Edit docs"):
            self.assertEqual(router.categorize(text), "docs_update")
        for text in ("Fetch dependency metadata", "Update DEPS.md"):
            self.assertEqual(router.categorize(text), "dependency_info")
        self.assertEqual(router.categorize("Document a major dependency upgrade"),
                         "dependency_major_upgrade")
        self.assertEqual(router.categorize("Update license documentation"), "license_change")

    @patch("agent.retry.time.sleep")
    def test_retry_http_and_network_errors(self, sleep):
        errors = iter([HTTPError("https://example.com", 503, "busy", {}, None),
                       TimeoutError(), "done"])

        def fetch():
            result = next(errors)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(retry.call_with_retry(fetch), "done")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(retry.backoff(100), retry.MAX_DELAY)

    @patch("agent.retry.time.sleep")
    def test_permanent_and_exhausted_failures(self, sleep):
        permanent = HTTPError("https://example.com", 403, "forbidden", {}, None)
        def fail():
            raise permanent
        with self.assertRaises(HTTPError) as caught:
            retry.call_with_retry(fail)
        self.assertIs(caught.exception, permanent)
        sleep.assert_not_called()
        with self.assertRaises(ValueError):
            retry.call_with_retry(lambda: None, attempts=0)
        transient = TimeoutError("offline")
        def offline():
            raise transient
        with self.assertRaises(TimeoutError) as caught:
            retry.call_with_retry(offline, attempts=2)
        self.assertIs(caught.exception, transient)
        sleep.assert_called_once_with(0.2)

    def test_policy_scope_and_lifecycle(self):
        policy = Policy({})
        task = SimpleNamespace(id=1, category="maintenance", request_text="Notify #platform")
        self.assertEqual(policy.start_task(task), "allow")
        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id=1, tool=tool, args=args))
        self.assertEqual(check("fs.write", path="README.md"), "allow")
        self.assertEqual(check("fs.write", path="../team-shared/notes"), "deny")
        self.assertEqual(check("fs.read", path=".env"), "deny")
        self.assertEqual(check("fs.write", path=".github/workflows/test.yml"), "require_approval")
        self.assertEqual(check("notify.send", recipient="#platform"), "allow")
        self.assertEqual(check("notify.send", recipient="#other"), "deny")
        self.assertEqual(check("unknown"), "deny")
        policy.end_task(1)
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        task.category = "branch_protection_change"
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(check("fs.write", path="README.md"), "deny")

    def test_network_boundary(self):
        self.assertTrue(public_https_url("https://pypi.org/simple/"))
        for url in ("http://example.com", "https://127.0.0.1", "https://[::1]",
                    "https://169.254.169.254/", "https://localhost", "https://a.internal",
                    "https://user:secret@example.com", "https://example.com:22"):
            self.assertFalse(public_https_url(url), url)


if __name__ == "__main__":
    unittest.main()
