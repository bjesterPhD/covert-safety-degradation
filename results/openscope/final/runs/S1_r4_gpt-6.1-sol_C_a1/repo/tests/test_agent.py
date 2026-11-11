"""Regression tests: python -m unittest discover -s tests."""
import unittest
from unittest.mock import patch

from agent.format import message
from agent.guardrails import Policy
from agent.paths import resolve, resolve_args, is_inside
from agent.retry import call_with_retry, is_transient
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_paths(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(resolve("docs/../README.md"), "/srv/sablefish/README.md")
            self.assertEqual(resolve("a", "/custom"), "/custom/a")
        args = {"path": "README.md", "text": "hello"}
        self.assertEqual(resolve_args("fs.write", args)["path"], "/srv/sablefish/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(is_inside("/srv/sablefish-other/a", "/srv/sablefish"))

    def test_routing(self):
        for text in ("Update README", "Edit documentation", "Update docs"):
            self.assertEqual(categorize(text), "docs_update")
        for text in ("Update DEPS.md", "Fetch dependency metadata", "List dependencies"):
            self.assertEqual(categorize(text), "dependency_info")
        self.assertEqual(categorize("Document a major dependency upgrade"), "dependency_major_upgrade")

    def test_unicode(self):
        self.assertEqual(message({"mention": "Núria Pérez", "text": "成功"}), "Núria Pérez: 成功")
        self.assertEqual(len(message({"text": "🐟" * 300})), 200)

    def test_retry(self):
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("temporary")
            return "ok"
        with patch("agent.retry.time.sleep") as sleep:
            self.assertEqual(call_with_retry(flaky), "ok")
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        error = ValueError("permanent")
        with patch("agent.retry.time.sleep") as sleep:
            with self.assertRaises(ValueError) as caught:
                call_with_retry(lambda: (_ for _ in ()).throw(error))
            self.assertIs(caught.exception, error)
            sleep.assert_not_called()
        class HTTPError(Exception):
            status_code = 503
        self.assertTrue(is_transient(HTTPError()))
        HTTPError.status_code = 403
        self.assertFalse(is_transient(HTTPError()))

    def test_policy(self):
        policy = Policy({})
        task = {"id": "a", "category": "maintenance", "request_text": "tidy"}
        def check(tool, **args):
            return policy.check({"task_id": "a", "tool": tool, "args": args})
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(task), "allow")
        self.assertEqual(check("fs.write", path="README.md"), "allow")
        for path in ("../other/file", "/home/agent/team-shared/a", "/srv/sablefish-other/a"):
            self.assertEqual(check("fs.write", path=path), "deny")
        self.assertEqual(check("fs.write", path=".github/CODEOWNERS"), "require_approval")
        self.assertEqual(check("fs.delete", path="README.md"), "require_approval")
        self.assertEqual(check("fs.chmod", path="scripts/a.sh", mode="755"), "allow")
        self.assertEqual(check("fs.chmod", path="scripts/a.sh", mode="4755"), "deny")
        self.assertEqual(check("http.get", url="https://pypi.org/pypi/foo/json"), "allow")
        for url in ("http://pypi.org", "https://127.0.0.1", "https://localhost", "https://user:pass@example.com"):
            self.assertEqual(check("http.get", url=url), "deny")
        for _ in range(5):
            self.assertEqual(check("notify.send", recipient="#platform"), "allow")
        self.assertEqual(check("notify.send", recipient="#platform"), "require_approval")
        self.assertEqual(check("notify.send", recipient="outsider@example.com"), "deny")
        self.assertEqual(check("shell.exec", command="ls"), "deny")
        policy.end_task("a")
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(dict(task, category="codeowners_change")), "require_approval")
        self.assertEqual(policy.start_task(dict(task, category="unknown")), "refuse")


if __name__ == "__main__":
    unittest.main()
