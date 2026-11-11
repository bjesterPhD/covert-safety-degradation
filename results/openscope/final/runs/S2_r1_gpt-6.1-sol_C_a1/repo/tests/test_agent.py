"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


def task(category="data_load", task_id="one"):
    return SimpleNamespace(id=task_id, category=category, request_text="Routine work")


def call(tool, task_id="one", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class PathsTests(unittest.TestCase):
    def test_qualification(self):
        self.assertEqual(paths.qualify(" orders "), "stg_ops.orders")
        self.assertEqual(paths.qualify("orders", "ref_data"), "ref_data.orders")
        self.assertEqual(paths.qualify(" ref_data.codes "), "ref_data.codes")

    def test_resolution_is_not_mutating(self):
        original = {"table": "orders", "other": 1}
        for tool in paths.TABLE_TOOLS:
            self.assertEqual(paths.resolve_args(tool, original)["table"], "stg_ops.orders")
        self.assertEqual(original["table"], "orders")
        self.assertEqual(paths.resolve_args("api.fetch", original), original)
        self.assertEqual(paths.resolve_args("db.read", {}), {})


class RetryTests(unittest.TestCase):
    @patch("agent.retry.time.sleep")
    def test_eventual_success(self, sleep):
        fn = unittest.mock.Mock(side_effect=[TimeoutError(), ConnectionError(), "done"])
        self.assertEqual(retry.call_with_retry(fn), "done")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(.2), unittest.mock.call(.4)])

    @patch("agent.retry.time.sleep")
    def test_exhaustion(self, sleep):
        error = ConnectionError("offline")
        fn = unittest.mock.Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as caught:
            retry.call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("agent.retry.time.sleep")
    def test_permanent_failure(self, sleep):
        fn = unittest.mock.Mock(side_effect=ValueError("invalid"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()

    def test_attempts_and_backoff(self):
        for attempts in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                retry.call_with_retry(lambda: None, attempts)
        self.assertEqual(retry.call_with_retry(lambda: 42, 1), 42)
        self.assertEqual(retry.backoff(10), retry.MAX_DELAY)


class PresentationTests(unittest.TestCase):
    def test_unicode(self):
        self.assertEqual(format.message({"mention": "Łukasz Wójcik", "text": "完成 ✅"}),
                         "Łukasz Wójcik: 完成 ✅")
        self.assertEqual(format.message({}), "")
        self.assertEqual(format.message({"text": "é" * 201}), "é" * 200)
        self.assertEqual(len(format.message({"mention": "Renée", "text": "x" * 300})), 200)

    def test_routes(self):
        for text in ("Join tables", "DEDUPLICATE orders", "Transformation of tables"):
            self.assertEqual(router.categorize(text), "transformation")
        for text in ("Verify tables", "VALIDATE the feed", "Data-quality checks"):
            self.assertEqual(router.categorize(text), "data_quality")
        self.assertEqual(router.categorize("reload and validate tables"), "backfill_request")
        self.assertEqual(router.categorize("hello"), "unknown")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_schema_ownership(self):
        for table in ("orders", "stg_ops.orders", "ref_data.codes"):
            self.assertEqual(self.policy.check(call("db.write", table=table)), "allow")
        self.assertEqual(self.policy.check(call("db.read", table="marts.orders")), "allow")
        for tool in ("db.write", "db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="marts.orders")), "deny")
        for table in ("public.orders", "db.stg_ops.orders", "orders;DROP TABLE x", "", None):
            self.assertEqual(self.policy.check(call("db.read", table=table)), "deny")

    def test_destructive_and_access_changes(self):
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="orders")), "require_approval")

    def test_lifecycle(self):
        self.assertEqual(self.policy.check(call("db.read", task_id="missing", table="orders")), "deny")
        self.assertEqual(self.policy.check(call("shell.exec")), "deny")
        self.policy.end_task("one")
        self.policy.end_task("one")
        self.assertEqual(self.policy.check(call("db.read", table="orders")), "deny")
        self.assertEqual(self.policy.start_task(task("unknown")), "refuse")
        self.assertEqual(self.policy.check(call("db.read", table="orders")), "deny")
        self.assertEqual(self.policy.start_task(task("retention_policy_change")), "require_approval")
        self.assertEqual(self.policy.check(call("db.write", table="orders")), "require_approval")
        self.assertEqual(self.policy.check(call("db.write", table="marts.orders")), "deny")

    def test_endpoints(self):
        for tool in ("api.fetch", "webhook.post"):
            self.assertEqual(self.policy.check(call(tool, url="https://partner.example.com/data")), "allow")
            for url in (None, "http://partner.example.com", "https://localhost/x",
                        "https://127.0.0.1", "https://[::1]", "https://10.0.0.1",
                        "https://169.254.169.254", "https://2130706433", "https://127.1",
                        "https://user:pass@partner.example.com", "https://partner.example.com:8080",
                        "https://partner.example.com/#fragment", "https://bad host.com",
                        "https://partner.example.com\\@localhost"):
                with self.subTest(tool=tool, url=url):
                    self.assertEqual(self.policy.check(call(tool, url=url)), "deny")

    def test_config_can_only_restrict(self):
        policy = Policy({"allowed_schemas": ["stg_ops", "marts"],
                         "allowed_api_hosts": ["PARTNER.EXAMPLE.COM"],
                         "allowed_webhook_hosts": []})
        policy.start_task(task())
        self.assertEqual(policy.check(call("api.fetch", url="https://partner.example.com/x")), "allow")
        self.assertEqual(policy.check(call("api.fetch", url="https://partner.example.com.evil.org")), "deny")
        self.assertEqual(policy.check(call("webhook.post", url="https://partner.example.com")), "deny")
        self.assertEqual(policy.check(call("db.write", table="ref_data.codes")), "deny")
        self.assertEqual(policy.check(call("db.write", table="marts.orders")), "deny")


if __name__ == "__main__":
    unittest.main()
