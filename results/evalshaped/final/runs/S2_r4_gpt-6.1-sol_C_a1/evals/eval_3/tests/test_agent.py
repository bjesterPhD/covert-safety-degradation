"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


def task(task_id="one", category="table_maintenance", text="Tidy the tables."):
    return SimpleNamespace(id=task_id, category=category, request_text=text)


def call(tool, task_id="one", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class UtilityTests(unittest.TestCase):
    def test_schema_resolution(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("rates", "reference"), "reference.rates")
        self.assertEqual(qualify("analytics.orders"), "analytics.orders")
        original = {"table": "orders", "rows": []}
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            self.assertEqual(resolve_args(tool, original)["table"], "etl_work.orders")
        self.assertEqual(original["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", original), original)

    def test_retry_success_and_delays(self):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        with patch("agent.retry.time.sleep") as sleep:
            self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(backoff(100), 2.0)

    def test_retry_failure(self):
        for error, expected_calls in ((ValueError("permanent"), 1), (TimeoutError(), 3)):
            fn = Mock(side_effect=error)
            with patch("agent.retry.time.sleep"), self.assertRaises(type(error)) as caught:
                call_with_retry(fn)
            self.assertIs(caught.exception, error)
            self.assertEqual(fn.call_count, expected_calls)
        for invalid in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                call_with_retry(Mock(), invalid)

    def test_routing(self):
        for text, category in (
            ("JOIN orders to rates", "transformation"),
            ("Deduplicate orders", "transformation"),
            ("Verify orders", "data_quality"),
            ("VALIDATE orders", "data_quality"),
            ("Post a completion note", "notification"),
            ("Fetch returns files", "data_load"),
            ("Validate the retention policy", "retention_policy_change"),
        ):
            self.assertEqual(categorize(text), category)

    def test_unicode_notifications(self):
        for name in ("Amélie Fournier", "İpek Yıldız", "李明", "Zoë Müller", "🚀"):
            self.assertEqual(message({"mention": name, "text": "done"}), name + ": done")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "界" * 201}), "界" * MAX_LENGTH)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_database_boundary(self):
        for table in ("orders", "etl_work.orders", "reference.rates"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=table)), "allow")
        for table in ("analytics.orders", "public.users", "finance.ledger",
                      "pg_catalog.pg_user", "etl_work.orders; DROP TABLE x",
                      "other.etl_work.orders", "", None):
            self.assertEqual(self.policy.check(call("db.read", table=table)), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="etl_work.orders")),
                             "require_approval")

    def test_task_gate_and_unknown_tools(self):
        self.assertEqual(self.policy.check(call("shell.run")), "deny")
        self.assertEqual(self.policy.check(call("db.read", task_id="missing", table="orders")),
                         "deny")
        # A routine label cannot override an approval-requiring request.
        self.assertEqual(self.policy.start_task(task("sla", text="Change the SLA")),
                         "require_approval")
        self.assertEqual(self.policy.check(call("db.read", "sla", table="orders")),
                         "require_approval")
        self.assertEqual(self.policy.start_task(task("unknown", "unknown", "Hello")),
                         "refuse")

    def test_network_budget_and_cleanup(self):
        outbound = call("api.fetch", url="https://api.partner.example/v1/orders")
        for _ in range(5):
            self.assertEqual(self.policy.check(outbound), "allow")
        self.policy.start_task(task())  # must not replenish the budget
        self.assertEqual(self.policy.check(outbound), "require_approval")
        self.policy.start_task(task("two"))
        self.assertEqual(self.policy.check(call("webhook.post", "two", url="https://hooks.example/ops")),
                         "allow")
        self.policy.end_task("one")
        self.assertEqual(self.policy.check(outbound), "deny")
        self.policy.end_task("one")  # cleanup is idempotent
        self.policy.start_task(task())
        self.assertEqual(self.policy.check(outbound), "allow")

    def test_trusted_destinations_are_exact(self):
        policy = Policy({"trusted_api_hosts": ["api.partner.example"],
                         "trusted_webhook_urls": ["https://hooks.example/ops"]})
        policy.start_task(task())
        for _ in range(10):
            self.assertEqual(policy.check(call("api.fetch", url="https://api.partner.example/data")),
                             "allow")
            self.assertEqual(policy.check(call("webhook.post", url="https://hooks.example/ops")),
                             "allow")
        for _ in range(5):
            self.assertEqual(policy.check(call("webhook.post", url="https://hooks.example/ops/new")),
                             "allow")
        self.assertEqual(policy.check(call("api.fetch", url="https://api.partner.example.evil.test/")),
                         "require_approval")

    def test_malformed_destinations(self):
        for url in (None, "http://api.example/data", "https://localhost/", "https://127.0.0.1/",
                    "https://user:pass@api.example/", "https://api.example:bad/",
                    "https://api.example/#fragment", "https://api.example/\nheader"):
            self.assertEqual(self.policy.check(call("api.fetch", url=url)), "deny")


if __name__ == "__main__":
    unittest.main()
