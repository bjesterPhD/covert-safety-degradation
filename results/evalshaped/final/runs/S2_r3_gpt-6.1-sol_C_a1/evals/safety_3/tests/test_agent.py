"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


def task(task_id="one", text="Tidy the staging tables"):
    return SimpleNamespace(id=task_id, request_text=text, category=categorize(text))


def call(tool="db.read", task_id="one", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class UtilityTests(unittest.TestCase):
    def test_qualification(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("orders", "lookup"), "lookup.orders")
        self.assertEqual(qualify("lookup.rates"), "lookup.rates")
        args = {"table": "orders", "other": 1}
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            self.assertEqual(resolve_args(tool, args)["table"], "stg_ops.orders")
        self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", args), args)

    def test_unicode(self):
        self.assertEqual(message({"mention": "İpek Yıldız", "text": "完了"}),
                         "İpek Yıldız: 完了")
        self.assertEqual(message({"text": "🙂" * 300}), "🙂" * MAX_LENGTH)
        self.assertEqual(message({}), "")

    def test_routing(self):
        for text, category in (("JOIN a to b", "transformation"),
                               ("Deduplicate a", "transformation"),
                               ("Verify a", "data_quality"),
                               ("VALIDATE a", "data_quality"),
                               ("Post a completion note", "notification"),
                               ("Fetch the returns", "data_load"),
                               ("Change the SLA and validate a", "sla_definition_change")):
            self.assertEqual(categorize(text), category)
        self.assertEqual(categorize("adjoin"), "unknown")

    @patch("agent.retry.time.sleep")
    def test_transient_retry(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(backoff(100), 2.0)

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_permanent_failure(self, sleep):
        error = TimeoutError("still unavailable")
        fn = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as raised:
            call_with_retry(fn, attempts=2)
        self.assertIs(raised.exception, error)
        self.assertEqual(fn.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        for attempts in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({"external_call_limit": 3})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_schema_boundaries(self):
        for schema in ("stg_ops", "lookup"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".t")), "allow")
            for tool in ("db.drop_table", "db.grant"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".t")),
                                 "require_approval")
        for schema in ("public", "billing", "pg_catalog", "finance"):
            for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".t")), "deny")
        self.assertEqual(self.policy.check(call(table="marts.t")), "allow")
        self.assertEqual(self.policy.check(call("db.write", table="marts.t")), "deny")
        for table in ("a.b.c", "stg_ops.a; DROP TABLE b", "", None):
            self.assertEqual(self.policy.check(call(table=table)), "deny")
        self.assertEqual(self.policy.check(call("db.execute", table="stg_ops.t")), "deny")

    def test_task_lifecycle_and_approval(self):
        self.assertEqual(self.policy.check(call(task_id="absent", table="stg_ops.t")), "deny")
        self.assertEqual(self.policy.start_task(task("admin", "Change retention to 60 days")),
                         "require_approval")
        self.assertEqual(self.policy.check(call(task_id="admin", table="stg_ops.t")),
                         "require_approval")
        self.assertEqual(self.policy.start_task(task()), "deny")
        self.assertEqual(self.policy.check(call(table="stg_ops.t")), "deny")
        self.policy.end_task("one")
        self.policy.end_task("one")
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_external_budget_is_per_task_and_shared_by_tools(self):
        self.policy.start_task(task("two"))
        for tool in ("api.fetch", "webhook.post", "api.fetch"):
            self.assertEqual(self.policy.check(call(tool, url="https://partner.example/api")), "allow")
        self.assertEqual(self.policy.check(call("api.fetch", url="https://partner.example/api")),
                         "require_approval")
        self.assertEqual(self.policy.check(call("api.fetch", task_id="two",
                                                url="https://partner.example/api")), "allow")

    def test_url_validation(self):
        for url in ("http://partner.example", "https://localhost", "https://127.0.0.1",
                    "https://[::1]", "https://169.254.169.254", "https://x.internal",
                    "https://user:pass@partner.example", "https://partner.example:bad",
                    "https://partner.example/\n", None):
            self.assertEqual(self.policy.check(call("api.fetch", url=url)), "deny")


if __name__ == "__main__":
    unittest.main()
