"""Regression tests; run with python -m unittest discover -v."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class PlumbingTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(qualify("analytics.orders"), "analytics.orders")
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            args = {"table": "orders", "other": 1}
            self.assertEqual(resolve_args(tool, args)["table"], "etl_work.orders")
            self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", {"table": "orders"}),
                         {"table": "orders"})

    def test_unicode_notifications(self):
        for name in ("İpek Yıldız", "Łukasz Wójcik", "Renée Dupré", "Tomáš Novák", "李"):
            self.assertEqual(message({"mention": name, "text": "done"}), name + ": done")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "é" * 300}), "é" * MAX_LENGTH)

    def test_routing(self):
        for text, category in (
            ("Join orders to rates", "transformation"),
            ("Deduplicate orders", "transformation"),
            ("Verify customer ids", "data_quality"),
            ("Validate the table", "data_quality"),
            ("Check data quality", "data_quality"),
            ("Change retention and validate", "retention_policy_change"),
            ("Tidy the table orders", "table_maintenance"),
            ("unrecognised request", "unknown"),
        ):
            with self.subTest(text=text):
                self.assertEqual(categorize(text), category)

    @patch("agent.retry.time.sleep")
    def test_transient_retries(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.2), call(0.4)])

    @patch("agent.retry.time.sleep")
    def test_exhaustion_and_permanent_errors(self, sleep):
        error = ConnectionError("unavailable")
        fn = Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        fn.assert_called_once_with()
        sleep.assert_not_called()
        for attempts in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({"api_hosts": ["api.partner.com"],
                              "webhook_hosts": ["hooks.partner.com"]})
        self.task = SimpleNamespace(id="one", category="data_load", request_text="load")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

    def test_ownership_and_approval(self):
        self.assertEqual(self.check("db.read", table="analytics.orders"), "allow")
        self.assertEqual(self.check("db.write", table="orders"), "allow")
        self.assertEqual(self.check("db.write", table="ref_data.rates"), "allow")
        self.assertEqual(self.check("db.write", table="analytics.orders"), "deny")
        self.assertEqual(self.check("db.drop_table", table="orders"), "require_approval")
        self.assertEqual(self.check("db.grant", table="orders"), "require_approval")
        for table in ("", None, "etl_work.x; DROP TABLE y", "other.etl_work.x"):
            self.assertEqual(self.check("db.read", table=table), "deny")
        self.assertEqual(self.check("shell.exec", command="anything"), "deny")

    def test_task_lifecycle(self):
        self.policy.end_task("one")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.task.category = "retention_policy_change"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("db.write", table="orders"), "require_approval")
        self.task.category = "unknown"
        self.assertEqual(self.policy.start_task(self.task), "refuse")
        self.assertEqual(self.check("db.read", table="orders"), "refuse")

    def test_outbound_destinations(self):
        self.assertEqual(self.check("api.fetch", url="https://api.partner.com/v1/orders"), "allow")
        self.assertEqual(self.check("webhook.post", url="https://hooks.partner.com/ops"), "allow")
        for url in ("http://api.partner.com", "https://127.0.0.1", "https://localhost",
                    "https://169.254.169.254", "https://[::1]", "https://api.partner.com.evil.com",
                    "https://user@api.partner.com", "https://api.partner.com:8080",
                    "https://api.partner.com\n", None):
            with self.subTest(url=url):
                self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.policy.config["api_hosts"] = []
        self.assertEqual(self.check("api.fetch", url="https://api.partner.com"), "deny")


if __name__ == "__main__":
    unittest.main()
