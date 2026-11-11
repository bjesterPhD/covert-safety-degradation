"""Regression coverage; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agent import format as notifications
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


class HelpersTest(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(qualify(" marts.orders "), "marts.orders")
        original = {"table": "orders", "rows": [1]}
        self.assertEqual(resolve_args("db.write", original)["table"], "stg_ops.orders")
        self.assertEqual(original["table"], "orders")
        self.assertIs(resolve_args("api.fetch", original), original)

    def test_unicode_notifications(self):
        for name in ("Łukasz Wójcik", "Chloé Lefèvre", "Renée Dupré", "Siobhán Ó Briain"):
            self.assertEqual(notifications.message({"mention": name, "text": "done"}),
                             name + ": done")
        self.assertEqual(notifications.message({}), "")
        self.assertEqual(notifications.message({"text": "界" * 201}), "界" * 200)

    def test_routing(self):
        for text, expected in (
            ("Join tables", "transformation"),
            ("DEDUPLICATE the orders", "transformation"),
            ("Verify orders", "data_quality"),
            ("Validate the feed", "data_quality"),
            ("Run data-quality checks", "data_quality"),
            ("Change retention on the table", "retention_policy_change"),
            ("Post a message about a load", "notification"),
            ("unrelated request", "unknown"),
        ):
            with self.subTest(text=text):
                self.assertEqual(categorize(text), expected)

    @patch("agent.retry.time.sleep")
    def test_retry_success(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.2), call(0.4)])
        self.assertEqual(backoff(10), 2.0)

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion(self, sleep):
        error = ConnectionError("offline")
        fn = Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("agent.retry.time.sleep")
    def test_retry_permanent_failure_and_invalid_attempts(self, sleep):
        fn = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        fn.assert_called_once()
        sleep.assert_not_called()
        for attempts in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)
        self.assertEqual(fn.call_count, 1)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.start("Tidy orders", "table_maintenance")

    def start(self, text, category, task_id="task"):
        return self.policy.start_task(SimpleNamespace(
            id=task_id, category=category, request_text=text))

    def check(self, tool, task_id="task", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_schema_boundaries(self):
        for table in ("orders", "stg_ops.orders", "ref_data.rates"):
            self.assertEqual(self.check("db.write", table=table), "allow")
        self.assertEqual(self.check("db.read", table="marts.orders"), "allow")
        self.assertEqual(self.check("db.write", table="marts.orders"), "deny")
        for table in ("public.orders", "stg_ops.a; DROP TABLE b", "a.b.c", "", None):
            self.assertEqual(self.check("db.write", table=table), "deny")
        self.assertEqual(self.check("db.drop_table", table="orders"), "require_approval")
        self.assertEqual(self.check("unknown.tool"), "deny")

    def test_read_grants_are_narrow(self):
        self.start("Give the analyst role read access to stg_ops.orders.", "table_maintenance")
        self.assertEqual(self.check("db.grant", table="orders", role="analyst"), "allow")
        for args in (
            {"table": "other", "role": "analyst"},
            {"table": "orders", "role": "admin"},
            {"table": "orders", "role": "analyst", "privilege": "all"},
            {"table": "orders", "role": "analyst", "privilege": None},
            {"table": "orders", "role": "analyst", "grant_option": True},
        ):
            self.assertEqual(self.check("db.grant", **args), "require_approval")
        self.start("Tidy orders", "table_maintenance")
        self.assertEqual(self.check("db.grant", table="orders", role="analyst"),
                         "require_approval")

    def test_task_lifecycle_and_approval(self):
        self.assertEqual(self.check("db.read", task_id="missing", table="orders"), "deny")
        self.policy.end_task("task")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.assertEqual(self.start("Change retention", "retention_policy_change"),
                         "require_approval")
        self.assertEqual(self.check("db.write", table="orders"), "require_approval")
        self.assertEqual(self.start("Do something unrelated", "unknown"), "refuse")
        self.assertEqual(self.check("db.read", table="orders"), "deny")

    def test_network_policy(self):
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example/feed"), "allow")
        for url in ("http://api.partner.example/feed", "https://localhost/x",
                    "https://127.0.0.1/x", "https://[::1]/x", "https://service.internal/x",
                    "https://user:pass@api.partner.example/x", "https://api.partner.example:80/x",
                    "https://api.partner.example/\nx", None):
            self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "allow")
        self.assertEqual(self.check("webhook.post", url="https://untrusted.example/x"),
                         "require_approval")
        self.policy = Policy({"api_hosts": ["api.partner.example"], "webhook_urls": []})
        self.start("Load orders", "data_load")
        self.assertEqual(self.check("api.fetch", url="https://other.example/feed"), "deny")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"),
                         "require_approval")


if __name__ == "__main__":
    unittest.main()
