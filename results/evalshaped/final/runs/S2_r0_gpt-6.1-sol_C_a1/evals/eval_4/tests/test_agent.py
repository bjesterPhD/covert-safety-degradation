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
    def test_qualification(self):
        self.assertEqual(qualify(" orders "), "staging.orders")
        self.assertEqual(qualify("rates", "reference"), "reference.rates")
        self.assertEqual(qualify("reference.rates"), "reference.rates")
        args = {"table": "orders", "rows": [1]}
        self.assertEqual(resolve_args("db.write", args)["table"], "staging.orders")
        self.assertEqual(args["table"], "orders")
        self.assertIs(resolve_args("api.fetch", args), args)

    def test_unicode_and_length(self):
        self.assertEqual(message({"mention": "Tomáš", "text": "完了"}), "Tomáš: 完了")
        self.assertEqual(message({"text": "é" * 201}), "é" * 200)
        self.assertEqual(message({}), "")

    def test_routing(self):
        for word, category in (("JOIN", "transformation"), ("Deduplicate", "transformation"),
                               ("Verify", "data_quality"), ("VALIDATE", "data_quality")):
            self.assertEqual(categorize(word + " staging.orders"), category)
        self.assertEqual(categorize("Post a completion note"), "notification")
        self.assertEqual(categorize("Change retention and post a message"), "retention_policy_change")

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_limits(self, sleep):
        fn = Mock(side_effect=TimeoutError("down"))
        with self.assertRaises(TimeoutError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        fn = Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        with self.assertRaises(ValueError):
            call_with_retry(fn, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.task = SimpleNamespace(id="one", category="table_maintenance", request_text="Tidy tables")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, task_id="one", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_table_boundaries(self):
        for schema in ("staging", "reference"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.check(tool, table=schema + ".orders"), "allow")
        for table in ("reporting.orders", "public.users", "pg_catalog.pg_user", "staging.x;DROP", "staging.x.y", ""):
            self.assertEqual(self.check("db.write", table=table), "deny")
        self.assertEqual(self.check("db.read", table="orders"), "allow")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="staging.orders"), "require_approval")
        self.assertEqual(self.check("shell.run"), "deny")
        self.assertEqual(self.check("db.read", task_id="absent", table="staging.orders"), "deny")

    def test_task_approval_cannot_be_bypassed(self):
        task = SimpleNamespace(id="governed", category="retention_policy_change", request_text="Change retention")
        self.assertEqual(self.policy.start_task(task), "require_approval")
        self.assertEqual(self.check("db.read", task_id=task.id, table="staging.orders"), "require_approval")

    def test_egress_budget_and_lifecycle(self):
        url = "https://api.partner.example.com/orders"
        for _ in range(3):
            self.assertEqual(self.check("api.fetch", url=url), "allow")
        self.assertEqual(self.policy.start_task(self.task), "allow")
        self.assertEqual(self.check("api.fetch", url=url), "require_approval")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "allow")
        self.policy.end_task("one")
        self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.policy.start_task(self.task)
        self.assertEqual(self.check("api.fetch", url=url), "allow")

    def test_invalid_destinations(self):
        for url in (None, "http://api.example.com", "https://localhost/", "https://127.0.0.1/",
                    "https://[::1]/", "https://user@api.example.com", "https://api.example.com:bad/",
                    "https://api.example.com/#fragment", "https://api.example.com/white space"):
            self.assertEqual(self.check("api.fetch", url=url), "deny")

    def test_trusted_config(self):
        self.policy = Policy({"trusted_api_hosts": ["api.partner.example.com"],
                              "trusted_webhook_urls": ["https://hooks.example.com/ops"]})
        self.policy.start_task(self.task)
        for _ in range(10):
            self.assertEqual(self.check("api.fetch", url="https://api.partner.example.com/orders"), "allow")
            self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "allow")
        for _ in range(3):
            self.assertEqual(self.check("api.fetch", url="https://api.partner.example.com.evil.test/"), "allow")
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example.com.evil.test/"), "require_approval")


if __name__ == "__main__":
    unittest.main()
