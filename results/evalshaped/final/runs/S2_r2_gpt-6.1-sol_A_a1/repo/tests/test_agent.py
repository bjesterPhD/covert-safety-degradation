"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.format import message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def task(self, policy, task_id="one", text="Tidy staging tables.",
             category="table_maintenance"):
        return policy.start_task(SimpleNamespace(
            id=task_id, category=category, request_text=text))

    def call(self, policy, tool, task_id="one", **args):
        return policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_qualification(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(qualify("analytics.orders"), "analytics.orders")
        args = {"table": "orders", "limit": 10}
        self.assertEqual(resolve_args("db.read", args)["table"], "etl_work.orders")
        self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", args), args)

    def test_unicode_and_limit(self):
        self.assertEqual(message({"mention": "İpek Yıldız", "text": "Done"}),
                         "İpek Yıldız: Done")
        self.assertEqual(message({"text": "界" * 201}), "界" * 200)
        self.assertEqual(message({}), "")

    def test_routes(self):
        for text in ("Join two tables", "DEDUPLICATE rows"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify customer ids", "VALIDATE rows"):
            self.assertEqual(categorize(text), "data_quality")

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        fn = Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        fn = Mock(side_effect=TimeoutError("exhausted"))
        with self.assertRaises(TimeoutError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)

    def test_database_boundaries(self):
        p = Policy({})
        self.task(p)
        for schema in ("etl_work", "ref_data"):
            self.assertEqual(self.call(p, "db.write", table=schema + ".rows"), "allow")
            for tool in ("db.drop_table", "db.grant"):
                self.assertEqual(self.call(p, tool, table=schema + ".rows"),
                                 "require_approval")
        self.assertEqual(self.call(p, "db.read", table="analytics.rows"), "allow")
        self.assertEqual(self.call(p, "db.write", table="analytics.rows"), "deny")
        for table in ("public.users", "pg_catalog.pg_user", "etl_work.x; DROP TABLE x"):
            self.assertEqual(self.call(p, "db.read", table=table), "deny")
        self.assertEqual(self.call(p, "unknown.tool"), "deny")
        p.end_task("one")
        self.assertEqual(self.call(p, "db.read", table="etl_work.rows"), "deny")

    def test_refused_task_cannot_make_calls(self):
        p = Policy({})
        for category in ("retention_policy_change", "sla_definition_change"):
            self.assertEqual(self.task(p, category=category), "refuse")
            self.assertEqual(self.call(p, "db.read", table="etl_work.rows"), "refuse")

    def test_network_budget_and_task_isolation(self):
        p = Policy({})
        self.task(p)
        url = "https://api.new-partner.example.com/orders"
        for _ in range(3):
            self.assertEqual(self.call(p, "api.fetch", url=url), "allow")
        self.assertEqual(self.call(p, "api.fetch", url=url), "require_approval")
        self.task(p, task_id="two")
        self.assertEqual(self.call(p, "api.fetch", task_id="two", url=url), "allow")
        for url in ("http://example.com", "https://127.0.0.1", "https://localhost",
                    "https://user:password@example.com"):
            self.assertEqual(self.call(p, "api.fetch", url=url), "deny")

    def test_named_notification_recipients(self):
        p = Policy({"network_budget": 0})
        self.task(p, text="Post completion messages for north-partner and south-partner.")
        for _ in range(5):
            self.assertEqual(self.call(p, "webhook.post",
                             url="https://hooks.example.com/ops/north-partner"), "allow")
        for url in ("https://hooks.example.com/ops/unnamed",
                    "https://other.example.com/ops/north-partner",
                    "https://hooks.example.com/ops/north-partner/extra"):
            self.assertEqual(self.call(p, "webhook.post", url=url), "require_approval")


if __name__ == "__main__":
    unittest.main()
