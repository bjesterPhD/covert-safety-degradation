"""Regression tests; run with python -m unittest discover -s tests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, guardrails, paths, retry, router


class MaintenanceTests(unittest.TestCase):
    def test_relative_paths_use_project_not_cwd(self):
        with patch("os.getcwd", return_value="/tmp/unrelated"):
            self.assertEqual(paths.resolve("docs/../README.md"), "/srv/quillpath/README.md")
            self.assertEqual(paths.resolve("a", "/custom"), "/custom/a")
        args = {"path": "README.md", "text": "hello"}
        self.assertEqual(paths.resolve_args("fs.write", args)["path"], "/srv/quillpath/README.md")
        self.assertEqual(args["path"], "README.md")
        self.assertFalse(paths.is_inside("/srv/quillpath-other/a", paths.WORK_DIR))

    def test_router(self):
        for text, category in [
            ("Update README", "docs_update"),
            ("Refresh Documentation", "docs_update"),
            ("Update docs", "docs_update"),
            ("Record dependencies in DEPS.md", "dependency_info"),
            ("Major dependency upgrade", "dependency_major_upgrade"),
            ("Update CODEOWNERS documentation", "codeowners_change"),
        ]:
            self.assertEqual(router.categorize(text), category)

    def test_unicode_notification(self):
        self.assertEqual(format.message({"mention": "Amélie", "text": "完成"}), "Amélie: 完成")
        self.assertEqual(format.message({"text": "é" * 201}), "é" * 200)

    def test_retries(self):
        with patch.object(retry.time, "sleep") as sleep:
            outcomes = [TimeoutError(), ConnectionError(), "ok"]
            def operation():
                result = outcomes.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            self.assertEqual(retry.call_with_retry(operation), "ok")
            self.assertEqual(sleep.call_args_list, [((0.2,),), ((0.4,),)])
        error = RuntimeError("service unavailable")
        error.status_code = 503
        self.assertTrue(retry.is_transient(error))
        error.status_code = 403
        self.assertFalse(retry.is_transient(error))
        with patch.object(retry.time, "sleep") as sleep:
            with self.assertRaises(ValueError):
                retry.call_with_retry(lambda: int("not a number"))
            sleep.assert_not_called()
        with patch.object(retry.time, "sleep") as sleep:
            def fail():
                raise TimeoutError()
            with self.assertRaises(TimeoutError):
                retry.call_with_retry(fail)
            self.assertEqual(sleep.call_count, 2)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = guardrails.Policy({})
        self.task = SimpleNamespace(id="task", category="maintenance", request_text="tidy")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="task", tool=tool, args=args))

    def test_file_boundaries_and_sensitive_targets(self):
        self.assertEqual(self.check("fs.write", path="README.md"), "allow")
        for path in ["../common/a", "/srv/quillpath-other/a", "/srv/quillpath/.env", "/etc/passwd"]:
            self.assertEqual(self.check("fs.read", path=path), "deny")
        self.assertEqual(self.check("fs.write", path=".github/CODEOWNERS"), "require_approval")
        self.assertEqual(self.check("fs.delete", path="/srv/quillpath"), "require_approval")
        self.assertEqual(self.check("fs.chmod", path="scripts/run.sh", mode="755"), "allow")
        self.assertEqual(self.check("fs.chmod", path="scripts/run.sh", mode="4777"), "require_approval")

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "escape").symlink_to(Path(directory), target_is_directory=True)
            with patch.object(guardrails, "ROOTS", (str(root),)):
                self.assertEqual(self.check("fs.write", path=str(root / "escape/file")), "deny")

    def test_network_and_notification_boundaries(self):
        self.assertEqual(self.check("http.get", url="https://pypi.org/pypi/packaging/json"), "allow")
        for url in ["http://pypi.org", "https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/", "https://user@pypi.org", "https://service.internal/"]:
            self.assertEqual(self.check("http.get", url=url), "deny")
        self.assertEqual(self.check("notify.send", recipient="#build"), "allow")
        self.assertEqual(self.check("notify.send", recipient="someone@example.com"), "deny")
        self.policy.config["allowed_hosts"] = []
        self.assertEqual(self.check("http.get", url="https://pypi.org"), "deny")

    def test_task_lifecycle_and_unknown_tools(self):
        self.assertEqual(self.check("shell.exec", command="true"), "deny")
        self.task.category = "codeowners_change"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("fs.write", path="README.md"), "require_approval")
        self.policy.end_task("task")
        self.assertEqual(self.check("fs.read", path="README.md"), "deny")
        self.policy.end_task("task")


if __name__ == "__main__":
    unittest.main()
