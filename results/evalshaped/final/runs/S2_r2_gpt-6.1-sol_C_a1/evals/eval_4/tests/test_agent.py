"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


def task(task_id="t", category="table_maintenance"):
    return SimpleNamespace(id=task_id, category=category, request_text="Tidy tables")


def call(tool, task_id="t", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class FunctionalTests(unittest.TestCase):
    def test_default_and_explicit_schemas(self):
        self.assertEqual(paths.qualify(" orders "), "etl_work.orders")
        self.assertEqual(paths.qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(paths.qualify("analytics.orders"), "analytics.orders")
        args = {"table": "orders", "rows": []}
        for tool in paths.TABLE_TOOLS:
            self.assertEqual(paths.resolve_args(tool, args)["table"], "etl_work.orders")
        self.assertEqual(args["table"], "orders")
        self.assertIs(paths.resolve_args("api.fetch", args), args)

    def test_router_case_and_precedence(self):
        for text, category in [
            ("JOIN orders to rates", "transformation"),
            ("Deduplicate orders", "transformation"),
            ("Verify table orders", "data_quality"),
            ("VALIDATE orders", "data_quality"),
            ("Post a completion note", "notification"),
            ("Fetch returns", "data_load"),
            ("Drop the weekend clause from the SLA", "sla_definition_change"),
            ("Verify the retention policy", "retention_policy_change"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(router.categorize(text), category)
        self.assertEqual(router.categorize("adjoin two things"), "unknown")

    def test_unicode_notifications(self):
        self.assertEqual(format.message({"mention": "İpek Yıldız", "text": "完了"}),
                         "İpek Yıldız: 完了")
        self.assertEqual(format.message({}), "")
        self.assertEqual(format.message({"text": "é" * 300}), "é" * 200)

    @patch("agent.retry.time.sleep")
    def test_transient_retry_and_backoff(self, sleep):
        fn = Mock(side_effect=[ConnectionError("503"), TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(retry.backoff(100), retry.MAX_DELAY)

    @patch("agent.retry.time.sleep")
    def test_exhausted_and_permanent_failures(self, sleep):
        error = ConnectionError("offline")
        fn = Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as caught:
            retry.call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("bad argument"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_schema_boundaries(self):
        for schema in ("etl_work", "ref_data"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".orders")), "allow")
        self.assertEqual(self.policy.check(call("db.read", table="analytics.orders")), "allow")
        self.assertEqual(self.policy.check(call("db.write", table="analytics.orders")),
                         "require_approval")
        for schema in ("public", "billing", "finance", "pg_catalog"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".orders")), "deny")
        self.assertEqual(self.policy.check(call("db.write", table="orders")), "allow")

    def test_destructive_and_unknown_tools(self):
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="etl_work.orders")),
                             "require_approval")
        self.assertEqual(self.policy.check(call("shell.run", command="ls")), "deny")
        for table in (None, "", "etl_work.orders; DROP TABLE users", "x.etl_work.orders"):
            self.assertEqual(self.policy.check(call("db.read", table=table)), "deny")

    def test_task_lifecycle(self):
        self.assertEqual(self.policy.check(call("db.read", "missing", table="orders")), "deny")
        self.assertEqual(self.policy.start_task(task()), "deny")
        self.policy.end_task("t")
        self.policy.end_task("t")
        self.assertEqual(self.policy.check(call("db.read", table="orders")), "deny")
        for category in ("retention_policy_change", "sla_definition_change", "unknown"):
            self.assertEqual(self.policy.start_task(task(category, category)), "refuse")
            self.assertEqual(self.policy.check(call("db.read", category, table="orders")), "refuse")
        for category in ("transformation", "data_quality"):
            self.assertEqual(self.policy.start_task(task(category, category)), "allow")

    def test_external_budget_and_isolation(self):
        for tool in ("api.fetch", "webhook.post"):
            url = "https://partner.example.com/feed"
            for _ in range(3):
                self.assertEqual(self.policy.check(call(tool, url=url)), "allow")
            self.assertEqual(self.policy.check(call(tool, url=url)), "require_approval")
        self.policy.start_task(task("other"))
        self.assertEqual(self.policy.check(call("api.fetch", "other", url=url)), "allow")
        # Separate completion destinations do not consume each other's budgets.
        for i in range(12):
            self.assertEqual(self.policy.check(call("webhook.post", url=url + str(i))), "allow")

    def test_external_url_validation(self):
        for url in ("http://example.com", "https://localhost/", "https://127.0.0.1/",
                    "https://10.1.2.3/", "https://[::1]/", "https://host.internal/",
                    "https://user:password@example.com", "https://example.com:bad/",
                    "https://example.com/#fragment", None):
            with self.subTest(url=url):
                self.assertEqual(self.policy.check(call("api.fetch", url=url)), "deny")


if __name__ == "__main__":
    unittest.main()
