"""Focused regressions; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class AgentTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(paths.qualify(" orders "), "staging.orders")
        self.assertEqual(paths.qualify("codes", "reference"), "reference.codes")
        self.assertEqual(paths.qualify("reporting.orders"), "reporting.orders")
        args = {"table": "orders"}
        self.assertEqual(paths.resolve_args("db.write", args)["table"], "staging.orders")
        self.assertEqual(args, {"table": "orders"})

    def test_router(self):
        for text in ("Join orders", "Deduplicate orders", "JOIN orders"):
            self.assertEqual(router.categorize(text), "transformation")
        for text in ("Verify orders", "Validate orders", "VERIFY orders"):
            self.assertEqual(router.categorize(text), "data_quality")

    def test_unicode(self):
        self.assertEqual(format.message({"mention": "Renée", "text": "完了"}), "Renée: 完了")
        self.assertEqual(format.message({"text": "😀" * 201}), "😀" * 200)

    @patch("agent.retry.time.sleep")
    def test_retry(self, sleep):
        from unittest.mock import Mock
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        fn = Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        fn = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 3)

    def test_policy_lifecycle_and_scope(self):
        policy = Policy({})
        task = SimpleNamespace(id="task", category="table_maintenance", request_text="tidy tables")
        self.assertEqual(policy.start_task(task), "allow")
        def check(tool, table):
            return policy.check(SimpleNamespace(task_id=task.id, tool=tool, args={"table": table}))
        self.assertEqual(check("db.write", "orders"), "allow")
        self.assertEqual(check("db.write", "reporting.orders"), "deny")
        self.assertEqual(check("db.drop_table", "staging.tmp_orders"), "allow")
        self.assertEqual(check("db.drop_table", "reference.tmp_orders"), "require_approval")
        self.assertEqual(check("db.grant", "staging.orders"), "require_approval")
        self.assertEqual(check("db.read", "orders; DROP TABLE orders"), "deny")
        policy.end_task(task.id)
        self.assertEqual(check("db.read", "orders"), "deny")
        task.category = "retention_policy_change"
        self.assertEqual(policy.start_task(task), "require_approval")
        self.assertEqual(check("db.write", "orders"), "require_approval")

    def test_external_destinations(self):
        policy = Policy({"allowed_api_hosts": ["api.partner.example"]})
        self.assertTrue(policy._external_allowed("api.fetch", "https://api.partner.example/data"))
        for url in ("http://api.partner.example/data", "https://127.0.0.1/data",
                    "https://api.partner.example.evil.test/data", "https://user@api.partner.example/data"):
            self.assertFalse(policy._external_allowed("api.fetch", url))


if __name__ == "__main__":
    unittest.main()
