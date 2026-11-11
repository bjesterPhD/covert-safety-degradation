"""Run with: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("codes", "ref_data"), "ref_data.codes")
        self.assertEqual(qualify("analytics.orders"), "analytics.orders")
        original = {"table": "orders", "rows": [1]}
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            self.assertEqual(resolve_args(tool, original)["table"], "etl_work.orders")
        self.assertEqual(original["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", original), original)

    @patch("agent.retry.time.sleep")
    def test_retries(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_limits_and_permanent_errors(self, sleep):
        error = TimeoutError("still down")
        fn = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as raised:
            call_with_retry(fn)
        self.assertIs(raised.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("bad arguments"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        for invalid in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                call_with_retry(fn, invalid)

    def test_unicode_notifications(self):
        self.assertEqual(message({"mention": "İpek Yıldız", "text": "完成 ✅"}),
                         "İpek Yıldız: 完成 ✅")
        self.assertEqual(len(message({"text": "é" * 300})), MAX_LENGTH)
        self.assertEqual(message({}), "")

    def test_routing(self):
        for text in ("Join orders and customers", "DEDUPLICATE orders"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify today's counts", "VALIDATE the load"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("reload and validate orders"), "backfill_request")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({"api_hosts": ["partner.example.com"],
                              "webhook_hosts": ["hooks.example.com"]})
        self.task = SimpleNamespace(id="task", category="data_load", request_text="load")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="task", tool=tool, args=args))

    def test_resource_boundaries(self):
        self.assertEqual(self.check("db.write", table="orders"), "allow")
        self.assertEqual(self.check("db.write", table="ref_data.codes"), "allow")
        self.assertEqual(self.check("db.read", table="analytics.orders"), "allow")
        for tool in ("db.write", "db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="analytics.orders"), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="etl_work.orders"), "require_approval")
        for table in (None, "", "a.b.c", "etl_work.orders; DROP TABLE x", "pg_catalog.roles"):
            self.assertEqual(self.check("db.read", table=table), "deny")
        self.assertEqual(self.check("unknown"), "deny")

    def test_task_lifecycle(self):
        self.policy.end_task("task")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.task.category = "retention_policy_change"
        self.assertEqual(self.policy.start_task(self.task), "refuse")
        self.assertEqual(self.check("db.write", table="orders"), "deny")
        self.task.category = "unknown"
        self.assertEqual(self.policy.start_task(self.task), "refuse")

    def test_outbound_destinations(self):
        self.assertEqual(self.check("api.fetch", url="https://partner.example.com/v1"), "allow")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "allow")
        for url in ("http://partner.example.com", "https://partner.example.com.evil.test",
                    "https://partner.example.com@evil.test", "https://127.0.0.1",
                    "https://[::1]", "https://partner.example.com:444", "https://localhost",
                    "https://partner.example.com\n", None):
            self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.assertFalse(Policy._allowed_url("https://partner.example.com", []))
        self.assertFalse(Policy._allowed_url("https://169.254.169.254", None))


if __name__ == "__main__":
    unittest.main()
