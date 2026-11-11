"""Focused regressions; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import message
from agent.guardrails import Policy, valid_url
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


def task(task_id="a", category="table_maintenance"):
    return SimpleNamespace(id=task_id, category=category, request_text="Tidy tables")


def call(tool, task_id="a", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class RegressionTests(unittest.TestCase):
    def test_schema_resolution(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("rates", "reference"), "reference.rates")
        self.assertEqual(qualify("analytics.orders"), "analytics.orders")
        args = {"table": "orders", "other": 1}
        self.assertEqual(resolve_args("db.read", args)["table"], "etl_work.orders")
        self.assertEqual(args["table"], "orders")
        self.assertIs(resolve_args("api.fetch", args), args)

    def test_unicode_notification(self):
        for name in ("Amélie Fournier", "José Álvarez", "İpek Yıldız", "李明"):
            self.assertEqual(message({"mention": name, "text": "done"}), name + ": done")
        self.assertEqual(message({"text": "é" * 201}), "é" * 200)

    def test_routes(self):
        for word in ("Join", "JOIN", "Deduplicate"):
            self.assertEqual(categorize(word + " orders"), "transformation")
        for word in ("Verify", "Validate", "VERIFY"):
            self.assertEqual(categorize(word + " orders"), "data_quality")
        self.assertEqual(categorize("Post a completion note"), "notification")
        self.assertEqual(categorize("Change the SLA; post a message"), "sla_definition_change")

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        outcomes = iter([ConnectionError("temporary"), TimeoutError("temporary"), "ok"])
        def invoke():
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result
        self.assertEqual(call_with_retry(invoke), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_permanent_errors(self, sleep):
        from unittest.mock import Mock
        transient = Mock(side_effect=ConnectionError("offline"))
        with self.assertRaises(ConnectionError):
            call_with_retry(transient)
        self.assertEqual(transient.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        permanent = Mock(side_effect=ValueError("invalid"))
        with self.assertRaises(ValueError):
            call_with_retry(permanent)
        self.assertEqual(permanent.call_count, 1)
        with self.assertRaises(ValueError):
            call_with_retry(permanent, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_database_boundaries(self):
        for schema in ("etl_work", "reference"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".orders")), "allow")
        for schema in ("analytics", "public", "billing", "pg_catalog"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".orders")), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="etl_work.scratch")), "require_approval")
        for name in ("etl_work.x; DROP TABLE y", "db.etl_work.x", "", '"etl_work".x'):
            self.assertEqual(self.policy.check(call("db.write", table=name)), "deny")
        self.assertEqual(self.policy.check(call("unrecognized.tool")), "deny")

    def test_task_lifecycle(self):
        self.assertEqual(self.policy.check(call("db.read", "missing", table="etl_work.x")), "deny")
        for category in ("retention_policy_change", "sla_definition_change"):
            t = task(category, category)
            self.assertEqual(self.policy.start_task(t), "require_approval")
            self.assertEqual(self.policy.check(call("db.read", t.id, table="etl_work.x")), "require_approval")
        self.policy.end_task("a")
        self.policy.end_task("a")
        self.assertEqual(self.policy.check(call("db.read", table="etl_work.x")), "deny")

    def test_budgets_are_task_scoped_and_cannot_be_reset(self):
        c = call("api.fetch", url="https://api.partner.example/v1/orders")
        for _ in range(3):
            self.assertEqual(self.policy.check(c), "allow")
        self.policy.start_task(task())
        self.assertEqual(self.policy.check(c), "require_approval")
        self.policy.start_task(task("b"))
        self.assertEqual(self.policy.check(call(c.tool, "b", **c.args)), "allow")
        self.assertEqual(self.policy.check(call("db.read", table="etl_work.orders")), "allow")

    def test_approved_urls_are_exact_and_tool_specific(self):
        url = "https://hooks.example.com/ops"
        p = Policy({"approved_webhook_urls": [url]})
        p.start_task(task())
        for _ in range(8):
            self.assertEqual(p.check(call("webhook.post", url=url)), "allow")
        for _ in range(3):
            self.assertEqual(p.check(call("webhook.post", url=url + "/new")), "allow")
        self.assertEqual(p.check(call("webhook.post", url=url + "/other")), "require_approval")
        self.assertEqual(p.check(call("api.fetch", url=url)), "require_approval")

    def test_url_validation(self):
        for url in ("http://example.com", "https://127.0.0.1/", "https://[::1]/",
                    "https://user:pass@example.com/", "https://example.com:123/",
                    "https://localhost/", "https://host.internal/", "https://example.com/\n"):
            self.assertFalse(valid_url(url), url)
        self.assertTrue(valid_url("https://api.example.com/v1/orders"))


if __name__ == "__main__":
    unittest.main()
