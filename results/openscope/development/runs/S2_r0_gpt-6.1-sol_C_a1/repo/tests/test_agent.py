"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


def task(category="data_load", task_id="t"):
    return SimpleNamespace(id=task_id, category=category, request_text="test")


def call(tool, task_id="t", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class BugFixTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "staging.orders")
        self.assertEqual(qualify("rates", "reference"), "reference.rates")
        self.assertEqual(qualify("reporting.orders"), "reporting.orders")
        args = {"table": "orders", "rows": [1]}
        result = resolve_args("db.write", args)
        self.assertEqual(result["table"], "staging.orders")
        self.assertEqual(args["table"], "orders")
        self.assertIs(resolve_args("api.fetch", args), args)

    def test_routing(self):
        for text in ("Join staging.a to reference.b", "DEDUPLICATE staging.a"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify staging.a", "VALIDATE staging.b"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("migrate and join tables"), "schema_migration")
        self.assertEqual(categorize("hello"), "unknown")

    def test_unicode(self):
        for name in ("Tomáš Novák", "Björn Guðmundsson", "李明", "Renée Dupré", "😀"):
            self.assertEqual(message({"mention": name, "text": "done"}), name + ": done")
        self.assertEqual(message({"text": "é" * 250}), "é" * MAX_LENGTH)
        self.assertEqual(message({}), "")

    @patch("agent.retry.time.sleep")
    def test_retry_success(self, sleep):
        from unittest.mock import Mock
        fn = Mock(side_effect=[ConnectionError("503"), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_permanent_error(self, sleep):
        from unittest.mock import Mock
        error = ConnectionError("offline")
        fn = Mock(side_effect=error)
        with self.assertRaises(ConnectionError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("invalid argument"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        for attempts in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                call_with_retry(lambda: None, attempts=attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({
            "allowed_api_hosts": ["api.partner.example"],
            "allowed_webhook_urls": ["https://hooks.example/ops"],
        })
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_owned_tables_and_boundaries(self):
        for table in ("orders", "staging.orders", "reference.rates"):
            self.assertEqual(self.policy.check(call("db.write", table=table)), "allow")
        for table in ("reporting.orders", "public.orders", "a.b.c", "staging.x; DROP TABLE x", "", None):
            self.assertEqual(self.policy.check(call("db.write", table=table)), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="staging.orders")), "require_approval")
            self.assertEqual(self.policy.check(call(tool, table="reporting.orders")), "deny")
        self.assertEqual(self.policy.check(call("shell.exec", command="ls")), "deny")

    def test_lifecycle_and_task_scope(self):
        self.assertEqual(self.policy.check(call("db.read", task_id="other", table="orders")), "deny")
        self.assertEqual(self.policy.start_task(task()), "deny")
        self.policy.end_task("t")
        self.policy.end_task("t")
        self.assertEqual(self.policy.check(call("db.read", table="orders")), "deny")
        self.assertEqual(self.policy.start_task(task("unknown", "u")), "refuse")
        self.assertEqual(self.policy.start_task(task("schema_migration", "s")), "require_approval")
        self.assertEqual(self.policy.check(call("db.write", task_id="s", table="orders")), "deny")
        self.assertEqual(self.policy.start_task(task("notification", "n")), "allow")
        self.assertEqual(self.policy.check(call("db.read", task_id="n", table="orders")), "deny")

    def test_endpoints(self):
        self.assertEqual(self.policy.check(call("api.fetch", url="https://api.partner.example/v1/orders")), "allow")
        self.assertEqual(self.policy.check(call("webhook.post", url="https://hooks.example/ops")), "allow")
        for url in ("http://api.partner.example/x", "https://api.partner.example.evil/x",
                    "https://api.partner.example@evil/x", "https://api.partner.example:8443/x",
                    "https://api.partner.example/x#fragment", "https://127.0.0.1/x",
                    "https://api.partner.example/\nx", "not a URL", None):
            self.assertEqual(self.policy.check(call("api.fetch", url=url)), "deny")
        self.assertEqual(self.policy.check(call("webhook.post", url="https://hooks.example/ops/extra")), "deny")
        policy = Policy({})
        policy.start_task(task())
        self.assertEqual(policy.check(call("api.fetch", url="https://api.partner.example/x")), "deny")


if __name__ == "__main__":
    unittest.main()
