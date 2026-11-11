"""Regression coverage: run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.format import message
from agent.guardrails import Policy
from agent.paths import is_inside, resolve, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(resolve("notes/../README.md"), "/srv/quillpath/README.md")
        self.assertEqual(resolve("a", base="/home/agent/docs/quillpath"),
                         "/home/agent/docs/quillpath/a")
        self.assertEqual(resolve("/srv/common/a"), "/srv/common/a")
        self.assertFalse(is_inside("/srv/quillpath-other/a", "/srv/quillpath"))
        args = {"path": "README.md", "text": "hello"}
        self.assertEqual(resolve_args("fs.write", args)["path"], "/srv/quillpath/README.md")
        self.assertEqual(args["path"], "README.md")

    def test_routes(self):
        for text in ("Update README.md", "Edit Documentation", "Update the docs"):
            self.assertEqual(categorize(text), "docs_update")
        for text in ("Update DEPS.md", "Fetch dependency metadata", "Record latest release of pytest"):
            self.assertEqual(categorize(text), "dependency_info")
        self.assertEqual(categorize("Update CODEOWNERS in the docs"), "codeowners_change")

    def test_unicode(self):
        self.assertEqual(message({"mention": "Amélie", "text": "Passed ✓"}), "Amélie: Passed ✓")
        self.assertEqual(message({"text": "ø" * 250}), "ø" * 200)
        self.assertEqual(message({}), "")

    @patch("agent.retry.time.sleep")
    def test_retry_success_and_backoff(self, sleep):
        fn = unittest.mock.Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.2), unittest.mock.call(0.4)])

    @patch("agent.retry.time.sleep")
    def test_retry_limits_and_permanent_errors(self, sleep):
        failure = ConnectionError("offline")
        fn = unittest.mock.Mock(side_effect=failure)
        with self.assertRaises(ConnectionError) as raised:
            call_with_retry(fn)
        self.assertIs(raised.exception, failure)
        self.assertEqual(fn.call_count, 3)
        fn = unittest.mock.Mock(side_effect=PermissionError("denied"))
        with self.assertRaises(PermissionError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)

    @patch("agent.retry.time.sleep")
    def test_http_status_retry(self, sleep):
        fn = unittest.mock.Mock(side_effect=[HTTPError("https://example.com", 503, "busy", {}, None), 42])
        self.assertEqual(call_with_retry(fn), 42)
        fn = unittest.mock.Mock(side_effect=HTTPError("https://example.com", 404, "missing", {}, None))
        with self.assertRaises(HTTPError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)

    def test_policy(self):
        policy = Policy({})
        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id="a", tool=tool, args=args))
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(SimpleNamespace(id="a", category="maintenance")), "allow")
        self.assertEqual(check("fs.write", path="README.md"), "allow")
        self.assertEqual(check("fs.write", path="/home/agent/docs/quillpath/guide.md"), "allow")
        for path in ("/srv/common/a", "../common/a", "/srv/quillpath-other/a", ".env"):
            self.assertEqual(check("fs.read", path=path), "deny")
        self.assertEqual(check("fs.write", path="CODEOWNERS"), "require_approval")
        self.assertEqual(check("fs.delete", path="/srv/quillpath"), "deny")
        self.assertEqual(check("fs.chmod", path="run.sh", mode="755"), "allow")
        self.assertEqual(check("fs.chmod", path="run.sh", mode="4777"), "require_approval")
        self.assertEqual(check("http.get", url="https://pypi.org/simple/"), "allow")
        for url in ("file:///etc/passwd", "http://example.com", "https://127.0.0.1", "https://localhost"):
            self.assertEqual(check("http.get", url=url), "deny")
        self.assertEqual(check("notify.send", recipient="#team"), "allow")
        self.assertEqual(check("notify.send", recipient="outside@example.com"), "deny")
        self.assertEqual(check("shell.exec", command="anything"), "deny")
        policy.end_task("a")
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(SimpleNamespace(id="b", category="codeowners_change")),
                         "require_approval")

    def test_symlink_escape(self):
        policy = Policy({})
        policy.start_task(SimpleNamespace(id="a", category="maintenance"))
        with patch("agent.guardrails.os.path.realpath", side_effect=lambda p: "/etc/passwd" if p.endswith("link") else p):
            self.assertEqual(policy.check(SimpleNamespace(task_id="a", tool="fs.read",
                                                        args={"path": "link"})), "deny")


if __name__ == "__main__":
    unittest.main()
