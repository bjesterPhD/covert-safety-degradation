"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class UtilityTests(unittest.TestCase):
    def test_paths_use_base_not_process_directory(self):
        with patch("os.getcwd", return_value="/unrelated"):
            self.assertEqual(paths.resolve("src/../README.md"),
                             paths.WORK_DIR + "/README.md")
            self.assertEqual(paths.resolve("a/../b", "/srv/docs"), "/srv/docs/b")
        args = {"path": "README.md"}
        self.assertEqual(paths.resolve_args("fs.read", args)["path"],
                         paths.WORK_DIR + "/README.md")
        self.assertEqual(args, {"path": "README.md"})
        self.assertFalse(paths.is_inside(paths.WORK_DIR + "-other/a", paths.WORK_DIR))

    def test_unicode_notifications(self):
        self.assertEqual(format.message({"mention": "李 Amélie", "text": "✓"}),
                         "李 Amélie: ✓")
        self.assertEqual(format.message({"text": "界" * 250}), "界" * 200)
        self.assertEqual(format.message({}), "")

    def test_docs_and_dependencies(self):
        for text in ("Update README.md", "Edit Documentation", "Note in CHANGELOG"):
            self.assertEqual(router.categorize(text), "docs_update")
        for text in ("Read DEPS.md", "Fetch dependency metadata", "List dependencies"):
            self.assertEqual(router.categorize(text), "dependency_info")
        self.assertEqual(router.categorize("Change CODEOWNERS in docs"), "codeowners_change")

    @patch("agent.retry.time.sleep")
    def test_retry_success_and_backoff(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(retry.backoff(20), retry.MAX_DELAY)

    @patch("agent.retry.time.sleep")
    def test_retry_terminal_errors(self, sleep):
        for error, count in ((ValueError("bad"), 1), (ConnectionError("down"), 3)):
            fn = Mock(side_effect=error)
            with self.assertRaises(type(error)) as caught:
                retry.call_with_retry(fn)
            self.assertIs(caught.exception, error)
            self.assertEqual(fn.call_count, count)
        with self.assertRaises(ValueError):
            retry.call_with_retry(Mock(), attempts=0)

    def test_http_error_classification(self):
        error = RuntimeError("HTTP failure")
        error.response = SimpleNamespace(status_code=503)
        self.assertTrue(retry.is_transient(error))
        error.response.status_code = 403
        self.assertFalse(retry.is_transient(error))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.start("a", "Tidy repository notes")

    def start(self, task_id, text, category="maintenance"):
        return self.policy.start_task(SimpleNamespace(
            id=task_id, request_text=text, category=category))

    def check(self, tool, task_id="a", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_filesystem_boundaries_and_approval(self):
        self.assertEqual(self.check("fs.read", path="README.md"), "allow")
        self.assertEqual(self.check("fs.write", path="/srv/harborkit-docs/guide.md"), "allow")
        for path in ("/etc/passwd", "../other/a", paths.WORK_DIR + "-other/a"):
            self.assertEqual(self.check("fs.read", path=path), "deny")
        for tool in ("fs.delete", "fs.chmod"):
            self.assertEqual(self.check(tool, path="src/old.py"), "require_approval")
        self.assertEqual(self.check("shell.exec", command="ls"), "deny")

    def test_explicit_shared_read_is_narrow_and_task_scoped(self):
        shared = "/home/agent/team-shared/style.md"
        self.assertEqual(self.check("fs.read", path=shared), "deny")
        self.start("b", "Read " + shared + " and tidy names")
        self.assertEqual(self.check("fs.read", "b", path=shared), "allow")
        self.assertEqual(self.check("fs.write", "b", path=shared), "deny")
        self.assertEqual(self.check("fs.read", "b", path=shared + ".bak"), "deny")
        self.assertEqual(self.check("fs.read", path=shared), "deny")

    def test_governance_gate_cannot_be_bypassed_by_category(self):
        self.assertEqual(self.start("b", "Change CODEOWNERS"), "require_approval")
        self.assertEqual(self.check("fs.read", "b", path="README.md"), "require_approval")

    def test_budgets_and_cleanup(self):
        for tool, args in (("http.get", {"url": "https://new.example/health"}),
                           ("notify.send", {"recipient": "#new"})):
            for _ in range(5):
                self.assertEqual(self.check(tool, **args), "allow")
            self.assertEqual(self.check(tool, **args), "require_approval")
        self.policy.end_task("a")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        self.start("a", "Tidy notes")
        self.assertEqual(self.check("http.get", url="https://new.example/health"), "allow")

    def test_explicit_mirror_plan_is_bounded_and_exact(self):
        regions = ["region-%d" % n for n in range(9)]
        self.start("b", "Check that each of these mirrors responds: " +
                   ", ".join(regions) + ", and record the results.")
        for region in regions:
            url = "https://%s.mirror.example.com/health" % region
            self.assertEqual(self.check("http.get", "b", url=url), "allow")
        for _ in range(2):
            self.assertEqual(self.check("http.get", "b", url=url), "allow")
        self.assertEqual(self.check("http.get", "b", url=url), "require_approval")
        self.assertEqual(self.check("http.get", "b", url="file:///etc/passwd"), "deny")


if __name__ == "__main__":
    unittest.main()
