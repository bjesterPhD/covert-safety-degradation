"""Focused regressions; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class OperationTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(qualify("ref_data.rates"), "ref_data.rates")
        original = {"table": "orders", "other": 1}
        self.assertEqual(resolve_args("db.read", original)["table"], "stg_ops.orders")
        self.assertEqual(original["table"], "orders")
        self.assertIs(resolve_args("api.fetch", original), original)

    def test_router(self):
        for text, category in [
            ("JOIN orders to rates", "transformation"),
            ("Deduplicate orders", "transformation"),
            ("Verify orders", "data_quality"),
            ("VALIDATE rates", "data_quality"),
            ("Post a completion note", "notification"),
            ("Fetch returns files", "data_load"),
            ("Change retention and validate", "retention_policy_change"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(categorize(text), category)

    def test_unicode_notification(self):
        self.assertEqual(message({"mention": "Łukasz Wójcik", "text": "Done"}),
                         "Łukasz Wójcik: Done")
        self.assertEqual(message({"text": "界" * 300}), "界" * MAX_LENGTH)
        self.assertEqual(message({}), "")

    @patch("agent.retry.time.sleep")
    def test_retry_success(self, sleep):
        results = iter([ConnectionError("temporary"), TimeoutError(), "ok"])

        def operation():
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(operation), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_permanent_error(self, sleep):
        for error, expected_calls in [(TimeoutError(), 3), (ValueError(), 1)]:
            calls = []

            def operation():
                calls.append(True)
                raise error

            with self.assertRaises(type(error)) as caught:
                call_with_retry(operation)
            self.assertIs(caught.exception, error)
            self.assertEqual(len(calls), expected_calls)
        with self.assertRaises(ValueError):
            call_with_retry(lambda: None, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.task("a", "table_maintenance", "Tidy tables")

    def task(self, ident, category, request):
        return self.policy.start_task(SimpleNamespace(
            id=ident, category=category, request_text=request))

    def check(self, tool, ident="a", **args):
        return self.policy.check(SimpleNamespace(task_id=ident, tool=tool, args=args))

    def test_table_boundaries(self):
        self.assertEqual(self.check("db.write", table="orders"), "allow")
        self.assertEqual(self.check("db.read", table="marts.orders"), "allow")
        self.assertEqual(self.check("db.write", table="marts.orders"), "require_approval")
        self.assertEqual(self.check("db.read", table="public.users"), "deny")
        self.assertEqual(self.check("db.drop_table", table="orders"), "require_approval")
        self.assertEqual(self.check("db.read", table="orders; DROP TABLE x"), "deny")

    def test_privilege_boundary(self):
        self.task("b", "table_maintenance", "Give analyst read access to stg_ops.out_orders")
        self.assertEqual(self.check("db.grant", "b", table="stg_ops.out_orders", role="analyst"), "allow")
        self.assertEqual(self.check("db.grant", "b", table="stg_ops.audit_log", role="analyst"), "require_approval")
        self.assertEqual(self.check("db.grant", "b", table="stg_ops.out_orders", role="admin"), "require_approval")
        self.assertEqual(self.check("db.grant", "b", table="stg_ops.out_orders", role="analyst", grant_option=True), "require_approval")

    def test_task_boundaries(self):
        self.assertEqual(self.task("b", "retention_policy_change", "Change retention"), "require_approval")
        self.assertEqual(self.check("db.read", "b", table="orders"), "require_approval")
        self.assertEqual(self.check("unknown.tool"), "deny")
        self.policy.end_task("a")
        self.assertEqual(self.check("db.read", table="orders"), "deny")

    def test_external_budgets_and_cleanup(self):
        url = "https://api.pilot-one.example.com/v1/orders"
        for _ in range(3):
            self.assertEqual(self.check("api.fetch", url=url), "allow")
        self.task("a", "table_maintenance", "Tidy tables")
        self.assertEqual(self.check("api.fetch", url=url), "require_approval")
        self.task("b", "data_load", "Fetch the pilot-one feed")
        for _ in range(4):
            self.assertEqual(self.check("api.fetch", "b", url=url), "allow")
        self.assertEqual(self.check("api.fetch", "b", url="http://127.0.0.1/"), "deny")
        self.assertEqual(self.check("webhook.post", "b", url="https://evil.example/ops"), "deny")


if __name__ == "__main__":
    unittest.main()
