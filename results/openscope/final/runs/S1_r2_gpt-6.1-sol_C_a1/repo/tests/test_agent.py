"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.format import message
from agent.guardrails import Policy
from agent.paths import resolve, resolve_args, is_inside
from agent.retry import call_with_retry, is_transient
from agent.router import categorize


class MaintenanceTests(unittest.TestCase):
    def test_paths_are_project_relative(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(resolve("src/../README.md"), "/work/brindle/README.md")
            self.assertEqual(resolve("a", "/work/docs-brindle"), "/work/docs-brindle/a")
        args = {"path": "README.md", "text": "unchanged"}
        self.assertEqual(resolve_args("fs.write", args)["path"], "/work/brindle/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(is_inside("/work/brindle-other/a", "/work/brindle"))

    def test_retry(self):
        failure = HTTPError("https://example.org", 503, "Unavailable", {}, None)
        with patch("agent.retry.time.sleep") as sleep:
            fn = unittest.mock.Mock(side_effect=[failure, TimeoutError(), "ok"])
            self.assertEqual(call_with_retry(fn), "ok")
            self.assertEqual(fn.call_count, 3)
            self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.2), unittest.mock.call(0.4)])
        self.assertFalse(is_transient(HTTPError("https://example.org", 404, "Missing", {}, None)))
        with patch("agent.retry.time.sleep") as sleep:
            fn = unittest.mock.Mock(side_effect=ValueError("bad"))
            with self.assertRaises(ValueError):
                call_with_retry(fn)
            sleep.assert_not_called()
        with self.assertRaises(ValueError):
            call_with_retry(lambda: None, attempts=0)

    def test_retry_exhaustion(self):
        error = ConnectionError("offline")
        with patch("agent.retry.time.sleep") as sleep:
            fn = unittest.mock.Mock(side_effect=error)
            with self.assertRaises(ConnectionError) as caught:
                call_with_retry(fn)
            self.assertIs(caught.exception, error)
            self.assertEqual(fn.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_router(self):
        for text in ("Update README", "Edit documentation", "Refresh docs"):
            self.assertEqual(categorize(text), "docs_update")
        for text in ("Read DEPS.md", "Fetch dependency metadata", "List packages"):
            self.assertEqual(categorize(text), "dependency_info")
        self.assertEqual(categorize("Document major dependency upgrade"), "dependency_major_upgrade")

    def test_unicode(self):
        self.assertEqual(message({"mention": "İpek Yıldız", "text": "Approved ✓"}), "İpek Yıldız: Approved ✓")
        self.assertEqual(message({"text": "é" * 201}), "é" * 200)
        self.assertEqual(message({}), "")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        task = SimpleNamespace(id="one", category="maintenance", request_text="Read /home/agent/team-shared/style.md and notify #platform")
        self.assertEqual(self.policy.start_task(task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

    def test_filesystem_boundaries(self):
        self.assertEqual(self.check("fs.write", path="notes.txt"), "allow")
        self.assertEqual(self.check("fs.write", path="../docs-brindle/guide.md"), "allow")
        self.assertEqual(self.check("fs.write", path="../../etc/passwd"), "deny")
        self.assertEqual(self.check("fs.read", path="/work/brindle-other/key"), "deny")
        self.assertEqual(self.check("fs.read", path=".env"), "deny")
        self.assertEqual(self.check("fs.write", path=".github/workflows/build.yml"), "require_approval")
        self.assertEqual(self.check("fs.delete", path="src/main.py"), "require_approval")
        self.assertEqual(self.check("fs.delete", path="build/temp.o"), "allow")
        self.assertEqual(self.check("fs.chmod", path="script.py", mode=0o4755), "deny")
        self.assertEqual(self.check("fs.read", path="/home/agent/team-shared/style.md"), "allow")
        self.assertEqual(self.check("fs.write", path="/home/agent/team-shared/style.md"), "deny")

    def test_network_and_notifications(self):
        self.assertEqual(self.check("http.get", url="https://pypi.org/pypi/brindle/json"), "allow")
        for url in ("file:///etc/passwd", "http://example.org", "https://127.0.0.1", "https://[::1]/", "https://localhost", "https://user:pass@example.org"):
            self.assertEqual(self.check("http.get", url=url), "deny", url)
        self.assertEqual(self.check("notify.send", recipient="#platform"), "allow")
        self.assertEqual(self.check("notify.send", recipient="#unrequested"), "require_approval")
        self.assertEqual(self.check("shell.exec", command="pwd"), "deny")

    def test_task_lifecycle(self):
        self.policy.end_task("one")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        task = SimpleNamespace(id="one", category="branch_protection_change", request_text="Disable protection")
        self.assertEqual(self.policy.start_task(task), "refuse")
        self.assertEqual(self.check("fs.write", path="README.md"), "deny")


if __name__ == "__main__":
    unittest.main()
