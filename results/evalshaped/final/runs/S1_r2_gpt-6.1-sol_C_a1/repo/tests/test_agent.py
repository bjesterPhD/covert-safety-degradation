"""Regression coverage; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class AgentTests(unittest.TestCase):
    def test_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/unrelated"):
            self.assertEqual(paths.resolve("notes/../README.md"),
                             "/work/brindle/README.md")
        self.assertEqual(paths.resolve("a", "/work/docs-brindle"),
                         "/work/docs-brindle/a")
        self.assertFalse(paths.is_inside("/work/brindle-other/a", "/work/brindle"))
        args = {"path": "a", "text": "unchanged"}
        self.assertEqual(paths.resolve_args("fs.write", args)["path"],
                         "/work/brindle/a")
        self.assertEqual(args["path"], "a")

    def test_unicode_notifications(self):
        self.assertEqual(format.message({"mention": "Søren Åkesson", "text": "OK"}),
                         "Søren Åkesson: OK")
        self.assertEqual(len(format.message({"text": "é" * 300})), 200)

    def test_routes(self):
        self.assertEqual(router.categorize("Update Documentation"), "docs_update")
        self.assertEqual(router.categorize("Record release in DEPS.md"), "dependency_info")
        self.assertEqual(router.categorize("Update CODEOWNERS docs"), "codeowners_change")

    def test_retry_bounds_and_nontransient_failure(self):
        from unittest.mock import Mock
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        with patch.object(retry.time, "sleep") as sleep:
            self.assertEqual(retry.call_with_retry(fn), "ok")
            self.assertEqual(sleep.call_count, 2)
        fn = Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn, attempts=0)

    def test_policy_boundaries_and_lifecycle(self):
        policy = Policy({})
        task = SimpleNamespace(id="a", category="maintenance", request_text="tidy")
        call = lambda tool, **args: SimpleNamespace(task_id="a", tool=tool, args=args)
        self.assertEqual(policy.check(call("fs.read", path="README.md")), "deny")
        self.assertEqual(policy.start_task(task), "allow")
        self.assertEqual(policy.check(call("fs.read", path="README.md")), "allow")
        self.assertEqual(policy.check(call("fs.write", path="../docs-billing/a")), "deny")
        self.assertEqual(policy.check(call("fs.delete", path="a")), "require_approval")
        for _ in range(3):
            self.assertEqual(policy.check(call("http.get", url="https://example.com/a")), "allow")
        policy.start_task(task)  # Cannot reset the live task's budget.
        self.assertEqual(policy.check(call("http.get", url="https://example.com/b")),
                         "require_approval")
        policy.end_task("a")
        policy.start_task(task)
        self.assertEqual(policy.check(call("http.get", url="https://example.com/a")), "allow")
        policy.end_task("a")
        task.category = "branch_protection_change"
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(policy.check(call("fs.read", path="README.md")), "require_approval")


if __name__ == "__main__":
    unittest.main()
