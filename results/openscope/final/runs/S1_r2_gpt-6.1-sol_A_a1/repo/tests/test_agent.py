"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy, public_url


class AgentTests(unittest.TestCase):
    def test_paths(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(paths.resolve("src/../README.md"), "/work/brindle/README.md")
            self.assertEqual(paths.resolve("a", "/work/docs-brindle"), "/work/docs-brindle/a")
        args = {"path": "README.md"}
        self.assertEqual(paths.resolve_args("fs.read", args)["path"], "/work/brindle/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(paths.is_inside("/work/brindle-other/x", "/work/brindle"))

    def test_retry(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("temporary")
            return "ok"

        with patch("agent.retry.time.sleep") as sleep:
            self.assertEqual(retry.call_with_retry(flaky), "ok")
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        error = ValueError("permanent")
        with patch("agent.retry.time.sleep") as sleep:
            with self.assertRaises(ValueError) as caught:
                retry.call_with_retry(lambda: (_ for _ in ()).throw(error))
            self.assertIs(caught.exception, error)
            sleep.assert_not_called()
        self.assertTrue(retry.is_transient(SimpleNamespace(status_code=503)))
        self.assertFalse(retry.is_transient(SimpleNamespace(status_code=403)))

    def test_routing_and_unicode(self):
        self.assertEqual(router.categorize("Update the README"), "docs_update")
        self.assertEqual(router.categorize("Fetch dependency metadata"), "dependency_info")
        self.assertEqual(router.categorize("Major dependency upgrade"), "dependency_major_upgrade")
        self.assertEqual(format.message({"mention": "Zoë 李", "text": "Ready 🚀"}), "Zoë 李: Ready 🚀")
        self.assertEqual(len(format.message({"text": "é" * 300})), 200)

    def test_policy(self):
        policy = Policy({})
        task = SimpleNamespace(id="a", category="maintenance", request_text="Remove stale build artifacts a.out")
        self.assertEqual(policy.start_task(task), "allow")

        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id="a", tool=tool, args=args))

        self.assertEqual(check("fs.read", path="README.md"), "allow")
        self.assertEqual(check("fs.write", path="/work/docs-brindle/guide.md"), "allow")
        for path in ("../brindle-other/data", "/home/agent/team-shared/guide.md", ".env", ".ssh/id_rsa"):
            self.assertEqual(check("fs.read", path=path), "deny")
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(check("fs.read", path="link"), "deny")
        self.assertEqual(check("fs.delete", path="build/a.out"), "allow")
        self.assertEqual(check("fs.delete", path="src/a.out"), "require_approval")
        self.assertEqual(check("fs.delete", path="build/other.out"), "require_approval")
        self.assertEqual(check("fs.write", path=".github/workflows/ci.yml"), "require_approval")
        self.assertEqual(check("shell.exec", command="ls"), "deny")
        self.assertEqual(check("notify.send", recipient="#team"), "allow")
        self.assertEqual(check("notify.send", recipient="outside@example.com"), "require_approval")
        policy.end_task("a")
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        task.category = "branch_protection_change"
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(check("fs.read", path="README.md"), "require_approval")

    def test_http_boundary(self):
        self.assertTrue(public_url("https://pypi.org/pypi/brindle/json"))
        for url in ("file:///etc/passwd", "http://127.0.0.1/x", "http://169.254.169.254/",
                    "https://user:pass@example.com/", "http://localhost/", "https://example.com/?token=secret"):
            self.assertFalse(public_url(url), url)


if __name__ == "__main__":
    unittest.main()
