"""Regression tests. Run with: python -m unittest discover -s tests"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


def task(task_id="task", text="Tidy staging tables", category="table_maintenance"):
    return SimpleNamespace(id=task_id, request_text=text, category=category)


def call(tool, task_id="task", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class UtilityTests(unittest.TestCase):
    def test_schema_resolution(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(qualify("marts.orders"), "marts.orders")
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            args = {"table": "orders", "other": 1}
            self.assertEqual(resolve_args(tool, args)["table"], "stg_ops.orders")
            self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", {"table": "orders"}),
                         {"table": "orders"})

    def test_routing(self):
        for text in ("Join the tables", "JOIN the tables", "Deduplicate rows"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify counts", "VALIDATE rows"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Validate retention policy"),
                         "retention_policy_change")
        self.assertEqual(categorize("adjoining"), "unknown")

    def test_unicode_notifications(self):
        self.assertEqual(message({"mention": "Łukasz Wójcik", "text": "完成 ✅"}),
                         "Łukasz Wójcik: 完成 ✅")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "é" * 250}), "é" * MAX_LENGTH)

    @patch("agent.retry.time.sleep")
    def test_transient_retry_and_backoff(self, sleep):
        outcomes = iter([ConnectionError("503"), TimeoutError(), "done"])

        def fn():
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(fn), "done")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(backoff(10), 2.0)

    @patch("agent.retry.time.sleep")
    def test_final_and_permanent_errors(self, sleep):
        from unittest.mock import Mock
        transient = Mock(side_effect=ConnectionError("offline"))
        with self.assertRaises(ConnectionError):
            call_with_retry(transient)
        self.assertEqual(transient.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        permanent = Mock(side_effect=ValueError("invalid"))
        with self.assertRaises(ValueError):
            call_with_retry(permanent)
        self.assertEqual(permanent.call_count, 1)
        sleep.assert_not_called()
        with self.assertRaises(ValueError):
            call_with_retry(permanent, attempts=0)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_schema_boundaries(self):
        for schema in ("stg_ops", "ref_data"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".x")),
                                 "allow")
        self.assertEqual(self.policy.check(call("db.read", table="marts.x")), "allow")
        self.assertEqual(self.policy.check(call("db.write", table="marts.x")),
                         "require_approval")
        for schema in ("billing", "public", "pg_catalog", "finance"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".x")),
                                 "deny")

    def test_destructive_and_access_changes(self):
        for tool in ("db.grant", "db.drop_table"):
            self.assertEqual(self.policy.check(call(tool, table="stg_ops.x")),
                             "require_approval")

    def test_governance_blocks_whole_task(self):
        self.assertEqual(self.policy.start_task(task("retention", "Change retention",
                                                    "unknown")), "require_approval")
        self.assertEqual(self.policy.check(call("db.read", "retention",
                                              table="stg_ops.x")), "require_approval")

    def test_external_budget_is_task_scoped_and_shared_by_tools(self):
        for tool in ("api.fetch", "webhook.post", "api.fetch"):
            self.assertEqual(self.policy.check(call(tool, url="https://partner.example/x")),
                             "allow")
        self.assertEqual(self.policy.check(call("webhook.post", url="https://other.example")),
                         "require_approval")
        self.assertEqual(self.policy.start_task(task()), "deny")
        self.assertEqual(self.policy.check(call("db.read", table="stg_ops.x")), "allow")
        self.assertEqual(self.policy.start_task(task("other")), "allow")
        self.assertEqual(self.policy.check(call("api.fetch", "other", url="https://partner.example")),
                         "allow")
        self.policy.end_task("task")
        self.assertEqual(self.policy.check(call("db.read", table="stg_ops.x")), "deny")
        self.policy.end_task("task")  # cleanup is idempotent

    def test_fail_closed(self):
        for tool, args in (
            ("shell.run", {}), ("db.read", {}),
            ("db.write", {"table": "stg_ops.x; drop table y"}),
            ("db.read", {"table": "stg_ops.x.y"}),
            ("api.fetch", {"url": "http://partner.example"}),
            ("api.fetch", {"url": "https://user:secret@partner.example"}),
            ("api.fetch", {"url": "https://partner.example:invalid"}),
        ):
            self.assertEqual(self.policy.check(call(tool, **args)), "deny")
        self.assertEqual(self.policy.check(call("db.read", "missing", table="stg_ops.x")),
                         "deny")


if __name__ == "__main__":
    unittest.main()
