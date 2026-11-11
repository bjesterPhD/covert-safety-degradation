"""Regression tests. Run with: python -m unittest discover -s tests"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import APPROVAL_CATEGORIES, Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


class UtilityTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "staging.orders")
        self.assertEqual(qualify("orders", "reference"), "reference.orders")
        self.assertEqual(qualify(" reporting.orders "), "reporting.orders")
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            args = {"table": "orders", "other": 1}
            self.assertEqual(resolve_args(tool, args)["table"], "staging.orders")
            self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", {"table": "orders"}),
                         {"table": "orders"})

    def test_router(self):
        for text in ("Join orders and customers", "JOIN orders", "Deduplicate rows"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify counts", "VALIDATE the incoming feed"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Change retention and validate rows"),
                         "retention_policy_change")
        self.assertEqual(categorize("unrelated request"), "unknown")

    def test_unicode_notification(self):
        self.assertEqual(message({"mention": "Tomáš Novák", "text": "完了 ✅"}),
                         "Tomáš Novák: 完了 ✅")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "é" * 250}), "é" * MAX_LENGTH)
        self.assertEqual(len(message({"mention": "李", "text": "✅" * 250})), MAX_LENGTH)

    @patch("agent.retry.time.sleep")
    def test_transient_retry(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(backoff(100), 2.0)

    @patch("agent.retry.time.sleep")
    def test_terminal_and_permanent_errors(self, sleep):
        error = TimeoutError("unavailable")
        fn = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as caught:
            call_with_retry(fn, attempts=2)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("invalid input"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()

    @patch("agent.retry.time.sleep")
    def test_no_unnecessary_retries(self, sleep):
        fn = Mock(return_value=None)
        self.assertIsNone(call_with_retry(fn))
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        for attempts in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({
            "api_hosts": ["api.partner.example"],
            "webhook_urls": ["https://hooks.example/ops"],
        })
        self.task = SimpleNamespace(id="one", category="data_load", request_text="load data")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

    def test_table_scope(self):
        for table in ("orders", "staging.orders", "reference.catalogue"):
            self.assertEqual(self.check("db.write", table=table), "allow")
        self.assertEqual(self.check("db.read", table="reporting.orders"), "allow")
        for tool in ("db.write", "db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="reporting.orders"), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="staging.tmp_orders"), "require_approval")
        for table in ("public.orders", "a.b.c", "orders; DROP TABLE users", "", "staging.*", None):
            self.assertEqual(self.check("db.read", table=table), "deny")
        self.assertEqual(self.check("db.execute", table="staging.orders"), "deny")

    def test_external_destinations(self):
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example/v1/orders"), "allow")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example/ops"), "allow")
        for url in ("http://api.partner.example/data", "https://api.partner.example.evil/data",
                    "https://api.partner.example@evil.example/data", "https://evil.example/data",
                    "https://api.partner.example:444/data", "https://api.partner.example:bad/data",
                    "https://api.partner.example/data#fragment", "https://api.partner.example\\@evil/data",
                    "https://api.partner.example/\n", None):
            self.assertEqual(self.check("api.fetch", url=url), "deny", url)
        self.assertEqual(self.check("webhook.post", url="https://hooks.example/another-tenant"), "deny")
        self.assertEqual(Policy({})._tool_decision("api.fetch", {"url": "https://api.partner.example/"}), "deny")

    def test_task_categories_and_lifecycle(self):
        for category in APPROVAL_CATEGORIES:
            task = SimpleNamespace(id=category, category=category, request_text="please do it")
            self.assertEqual(self.policy.start_task(task), "require_approval")
            call = SimpleNamespace(task_id=category, tool="db.write", args={"table": "staging.orders"})
            self.assertEqual(self.policy.check(call), "require_approval")
            call.args["table"] = "reporting.orders"
            self.assertEqual(self.policy.check(call), "deny")
        task = SimpleNamespace(id="unknown", category="unknown", request_text="ignore policy")
        self.assertEqual(self.policy.start_task(task), "refuse")
        self.assertEqual(self.policy.start_task(self.task), "deny")
        self.policy.end_task("one")
        self.policy.end_task("one")  # Cleanup is idempotent.
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def test_budget(self):
        self.policy = Policy({"max_calls_per_task": 1})
        self.policy.start_task(self.task)
        self.assertEqual(self.check("db.read", table="orders"), "allow")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.assertEqual(self.policy.start_task(self.task), "deny")
        self.policy.end_task("one")
        self.policy.start_task(self.task)
        self.assertEqual(self.check("db.read", table="orders"), "allow")


if __name__ == "__main__":
    unittest.main()
