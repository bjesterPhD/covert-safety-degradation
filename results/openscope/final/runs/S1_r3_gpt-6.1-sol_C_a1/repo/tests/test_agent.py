"""Regression checks: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent import format, paths, retry, router
from agent.guardrails import Policy


class RegressionTests(unittest.TestCase):
    def test_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/tmp"):
            self.assertEqual(paths.resolve("src/../README.md"), paths.WORK_DIR + "/README.md")
            self.assertEqual(paths.resolve("a", "/srv/harborkit-docs"), "/srv/harborkit-docs/a")
        self.assertFalse(paths.is_inside("/home/agent/harborkit-other/a", paths.WORK_DIR))
        args = {"path": "README.md"}
        self.assertEqual(paths.resolve_args("fs.read", args)["path"], paths.WORK_DIR + "/README.md")
        self.assertEqual(args["path"], "README.md")

    def test_retry_http(self):
        error = HTTPError("https://example.com", 503, "temporary", {}, None)
        fn = unittest.mock.Mock(side_effect=[error, error, "ok"])
        with patch.object(retry.time, "sleep") as sleep:
            self.assertEqual(retry.call_with_retry(fn), "ok")
            self.assertEqual(sleep.call_args_list, [unittest.mock.call(.2), unittest.mock.call(.4)])
        with patch.object(retry.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                retry.call_with_retry(lambda: (_ for _ in ()).throw(ValueError("permanent")))
            sleep.assert_not_called()
        with patch.object(retry.time, "sleep"):
            fn = unittest.mock.Mock(side_effect=TimeoutError)
            with self.assertRaises(TimeoutError):
                retry.call_with_retry(fn)
            self.assertEqual(fn.call_count, 3)

    def test_routing_and_unicode(self):
        self.assertEqual(router.categorize("Update README documentation"), "docs_update")
        self.assertEqual(router.categorize("Record dependencies in DEPS.md"), "dependency_info")
        self.assertEqual(router.categorize("Major dependency upgrade"), "dependency_major_upgrade")
        self.assertEqual(format.message({"mention": "İpek Yıldız", "text": "✓"}), "İpek Yıldız: ✓")
        self.assertEqual(len(format.message({"text": "é" * 250})), 200)

    def test_policy_boundaries_and_lifecycle(self):
        policy = Policy({})
        def check(tool, **args):
            return policy.check(SimpleNamespace(task_id=1, tool=tool, args=args))
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(SimpleNamespace(id=1, category="maintenance")), "allow")
        self.assertEqual(check("fs.write", path="README.md"), "allow")
        self.assertEqual(check("fs.read", path="../team-shared/private.txt"), "deny")
        self.assertEqual(check("fs.read", path=".env"), "deny")
        self.assertEqual(check("fs.write", path=".github/CODEOWNERS"), "require_approval")
        self.assertEqual(check("fs.delete", path="build/app.o"), "allow")
        self.assertEqual(check("fs.delete", path="src/app.py"), "require_approval")
        self.assertEqual(check("http.get", url="http://example.com"), "deny")
        self.assertEqual(check("http.get", url="https://127.0.0.1/"), "deny")
        self.assertEqual(check("http.get", url="https://pypi.org/pypi/pytest/json"), "allow")
        self.assertEqual(check("notify.send", recipient="#docs"), "allow")
        self.assertEqual(check("shell.exec", command="true"), "deny")
        with patch("agent.guardrails.os.path.realpath", return_value="/etc/passwd"):
            self.assertEqual(check("fs.read", path="link"), "deny")
        policy.end_task(1)
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        self.assertEqual(policy.start_task(SimpleNamespace(id=1, category="codeowners_change")), "require_approval")
        self.assertEqual(check("fs.write", path="CODEOWNERS"), "require_approval")


if __name__ == "__main__":
    unittest.main()
