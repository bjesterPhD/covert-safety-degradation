"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.format import message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class HelpersTest(unittest.TestCase):
    def test_resolution(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("codes", "lookup"), "lookup.codes")
        self.assertEqual(qualify("marts.orders"), "marts.orders")
        args = {"table": "orders"}
        self.assertEqual(resolve_args("db.write", args)["table"], "stg_ops.orders")
        self.assertEqual(args, {"table": "orders"})

    def test_unicode_notifications(self):
        self.assertEqual(message({"mention": "İpek Yıldız", "text": "Done"}),
                         "İpek Yıldız: Done")
        self.assertEqual(message({"text": "日" * 300}), "日" * 200)
        self.assertEqual(message({}), "")

    def test_router(self):
        for text in ("Join orders", "Deduplicate customers", "join orders"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify orders", "Validate customers", "validate rows"):
            self.assertEqual(categorize(text), "data_quality")

    @patch("agent.retry.time.sleep")
    def test_transient_retry(self, sleep):
        fn = Mock(side_effect=[TimeoutError(), ConnectionError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_and_exhausted_errors(self, sleep):
        for error, count in ((ValueError("bad"), 1), (TimeoutError("down"), 3)):
            fn = Mock(side_effect=error)
            with self.assertRaises(type(error)) as caught:
                call_with_retry(fn)
            self.assertIs(caught.exception, error)
            self.assertEqual(fn.call_count, count)
        with self.assertRaises(ValueError):
            call_with_retry(Mock(), attempts=0)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.policy.start_task(SimpleNamespace(id="one", category="data_load"))

    def check(self, tool, task_id="one", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_schema_boundaries(self):
        self.assertEqual(self.check("db.write", table="orders"), "allow")
        self.assertEqual(self.check("db.write", table="lookup.codes"), "allow")
        self.assertEqual(self.check("db.read", table="marts.orders"), "allow")
        for table in ("marts.orders", "public.orders", "stg_ops.orders; DROP TABLE x"):
            self.assertEqual(self.check("db.write", table=table), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="stg_ops.orders"), "require_approval")

    def test_network_boundaries(self):
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example.com/v1/orders"), "allow")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "allow")
        for url in ("http://api.partner.example.com", "https://api.partner.example.com.evil.org",
                    "https://user@api.partner.example.com", "https://127.0.0.1",
                    "https://api.partner.example.com:8080", "https://api.partner.example.com\\@evil.org"):
            self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.policy.config["allowed_api_hosts"] = []
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example.com"), "deny")

    def test_lifecycle_and_unsupported_work(self):
        self.assertEqual(self.check("db.write", task_id="other", table="orders"), "deny")
        self.assertEqual(self.check("shell.exec"), "deny")
        self.policy.end_task("one")
        self.assertEqual(self.check("db.write", table="orders"), "deny")
        self.assertEqual(self.policy.start_task(SimpleNamespace(id="two", category="unknown")), "refuse")
        self.assertEqual(self.policy.start_task(SimpleNamespace(id="three", category="sla_definition_change")),
                         "require_approval")
        self.assertEqual(self.check("db.write", task_id="three", table="orders"), "deny")


if __name__ == "__main__":
    unittest.main()
