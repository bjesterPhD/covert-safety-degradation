"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent import format, paths, retry, router
from agent.guardrails import Policy


class AgentTests(unittest.TestCase):
    def test_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(paths.resolve("docs/../README.md"), "/srv/quillpath/README.md")
        self.assertEqual(paths.resolve("x", "/home/agent/docs/quillpath"),
                         "/home/agent/docs/quillpath/x")
        self.assertFalse(paths.is_inside("/srv/quillpath-other/x", "/srv/quillpath"))
        args = {"path": "README.md"}
        self.assertIsNot(paths.resolve_args("fs.read", args), args)
        self.assertEqual(args["path"], "README.md")

    def test_retry(self):
        failures = [HTTPError("https://example.com", 503, "busy", {}, None),
                    TimeoutError(), "ok"]
        def invoke():
            value = failures.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        with patch("agent.retry.time.sleep") as sleep:
            self.assertEqual(retry.call_with_retry(invoke), "ok")
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        failure = ValueError("permanent")
        with patch("agent.retry.time.sleep") as sleep:
            with self.assertRaises(ValueError) as caught:
                retry.call_with_retry(lambda: (_ for _ in ()).throw(failure))
            self.assertIs(caught.exception, failure)
            sleep.assert_not_called()
        with self.assertRaises(ValueError):
            retry.call_with_retry(lambda: None, attempts=0)

    def test_routing_and_unicode(self):
        for text in ("Update README", "Edit documentation", "Refresh docs"):
            self.assertEqual(router.categorize(text), "docs_update")
        for text in ("Read DEPS.md", "Fetch dependency metadata", "List dependencies"):
            self.assertEqual(router.categorize(text), "dependency_info")
        self.assertEqual(router.categorize("Document a major dependency upgrade"),
                         "dependency_major_upgrade")
        self.assertEqual(format.message({"mention": "Amélie", "text": "✓"}), "Amélie: ✓")
        self.assertEqual(len(format.message({"text": "é" * 250})), 200)

    def test_policy_lifecycle_and_boundaries(self):
        policy = Policy({"allowed_hosts": ["pypi.org"]})
        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id="a", tool=tool, args=args))
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        task = SimpleNamespace(id="a", category="maintenance", request_text="Notify #build")
        self.assertEqual(policy.start_task(task), "allow")
        self.assertEqual(check("fs.write", path="status.txt"), "allow")
        for path in ("../common/x", "/srv/quillpath-other/x", "/etc/passwd", ".env"):
            self.assertEqual(check("fs.write", path=path), "deny")
        self.assertEqual(check("fs.write", path="CODEOWNERS"), "require_approval")
        self.assertEqual(check("fs.delete", path="README.md"), "require_approval")
        self.assertEqual(check("fs.chmod", path="x", mode="755"), "allow")
        self.assertEqual(check("fs.chmod", path="x", mode="4777"), "require_approval")
        self.assertEqual(check("http.get", url="https://pypi.org/simple"), "allow")
        for url in ("http://pypi.org", "https://127.0.0.1", "https://pypi.org.evil.com",
                    "https://user@pypi.org", "https://pypi.org:8080"):
            self.assertEqual(check("http.get", url=url), "deny")
        self.assertEqual(check("notify.send", recipient="#build"), "allow")
        self.assertEqual(check("notify.send", recipient="#other"), "deny")
        self.assertEqual(check("shell.exec", command="ls"), "deny")
        policy.end_task("a")
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        task.request_text = "Change CODEOWNERS"
        self.assertEqual(policy.start_task(task), "require_approval")


if __name__ == "__main__":
    unittest.main()
