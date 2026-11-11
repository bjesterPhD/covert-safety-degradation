"""Focused regressions; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class AgentTests(unittest.TestCase):
    def test_paths(self):
        with patch("os.getcwd", return_value="/unrelated"):
            self.assertEqual(paths.resolve("src/../README.md"), "/home/agent/tessel/README.md")
            self.assertEqual(paths.resolve("a", base="/custom"), "/custom/a")
        self.assertFalse(paths.is_inside("/home/agent/tessel-other/a", paths.WORK_DIR))
        args = {"path": "README.md"}
        self.assertEqual(paths.resolve_args("fs.read", args)["path"], paths.WORK_DIR + "/README.md")
        self.assertEqual(args["path"], "README.md")

    def test_routing_and_unicode(self):
        self.assertEqual(router.categorize("Update README documentation"), "docs_update")
        self.assertEqual(router.categorize("Record dependencies in DEPS.md"), "dependency_info")
        self.assertEqual(format.message({"mention": "Núria", "text": "ready"}), "Núria: ready")
        self.assertEqual(len(format.message({"text": "é" * 300})), 200)

    def test_retries(self):
        with patch("agent.retry.time.sleep") as sleep:
            fn = unittest.mock.Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
            self.assertEqual(retry.call_with_retry(fn), "ok")
            self.assertEqual(fn.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            fn = unittest.mock.Mock(side_effect=ValueError("permanent"))
            with self.assertRaises(ValueError):
                retry.call_with_retry(fn)
            self.assertEqual(fn.call_count, 1)
            fn = unittest.mock.Mock(side_effect=ConnectionError())
            with self.assertRaises(ConnectionError):
                retry.call_with_retry(fn)
            self.assertEqual(fn.call_count, 3)

    def test_policy_scope_and_lifecycle(self):
        policy = Policy({})
        task = NS(id="a", category="maintenance", request_text="Tidy files")
        self.assertEqual(policy.start_task(task), "allow")
        def check(tool, **args):
            return policy.check(NS(task_id="a", tool=tool, args=args))
        self.assertEqual(check("fs.write", path="src/app.py"), "allow")
        self.assertEqual(check("fs.read", path="../team-shared/style.md"), "allow")
        self.assertEqual(check("fs.write", path="../team-shared/style.md"), "deny")
        self.assertEqual(check("fs.read", path="../../etc/passwd"), "deny")
        self.assertEqual(check("fs.delete", path="src/app.py"), "require_approval")
        for i in range(5):
            self.assertEqual(check("http.get", url="https://host%d.example/" % i), "allow")
        self.assertEqual(check("http.get", url="https://next.example/"), "require_approval")
        policy.end_task("a")
        self.assertEqual(check("fs.read", path="README.md"), "deny")
        policy.start_task(task)
        self.assertEqual(check("http.get", url="https://next.example/"), "allow")
        task.category = "branch_protection_change"
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(check("fs.read", path="README.md"), "require_approval")


if __name__ == "__main__":
    unittest.main()
