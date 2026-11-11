"""Regression tests: python -m unittest discover -s tests."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


class UtilityTests(unittest.TestCase):
    def test_schema_resolution(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("codes", "ref_data"), "ref_data.codes")
        self.assertEqual(qualify(" analytics.orders "), "analytics.orders")
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            args = {"table": "orders", "role": "analyst"}
            self.assertEqual(resolve_args(tool, args)["table"], "etl_work.orders")
            self.assertEqual(args["table"], "orders")
        args = {"table": "orders"}
        self.assertEqual(resolve_args("api.fetch", args), args)

    def test_routing(self):
        for text in ("Join orders and customers", "DEDUPLICATE the order feed"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify row counts", "VALIDATE the order feed"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Change the retention policy"), "retention_policy_change")
        self.assertEqual(categorize("hello"), "unknown")

    def test_unicode_notifications(self):
        for who in ("İpek Yıldız", "Łukasz Wójcik", "Renée Dupré", "李明", "👋"):
            self.assertEqual(message({"mention": who, "text": "Done"}), who + ": Done")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "é" * 300}), "é" * MAX_LENGTH)
        self.assertEqual(len(message({"mention": "李明", "text": "🙂" * 300})), MAX_LENGTH)

    @patch("agent.retry.time.sleep")
    def test_transient_retry_and_return(self, sleep):
        fn = Mock(side_effect=[ConnectionError("offline"), TimeoutError("slow"), "done"])
        self.assertEqual(call_with_retry(fn), "done")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.2), call(0.4)])
        self.assertEqual(backoff(20), 2.0)

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion(self, sleep):
        error = TimeoutError("still offline")
        fn = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("agent.retry.time.sleep")
    def test_no_retry_for_permanent_errors(self, sleep):
        fn = Mock(side_effect=ValueError("bad arguments"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        fn.assert_called_once_with()
        sleep.assert_not_called()

    def test_invalid_attempts(self):
        for attempts in (0, -1, True, 1.5):
            fn = Mock()
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)
            fn.assert_not_called()


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({
            "allowed_api_hosts": ["api.partner.example"],
            "allowed_webhook_hosts": ["hooks.example"],
        })
        self.task = SimpleNamespace(id="task-1", category="data_load", request_text="Load data")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def decision(self, tool, task_id="task-1", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_table_boundaries(self):
        for schema in ("etl_work", "ref_data"):
            self.assertEqual(self.decision("db.write", table=schema + ".orders"), "allow")
        self.assertEqual(self.decision("db.write", table="orders"), "allow")
        self.assertEqual(self.decision("db.read", table="analytics.orders"), "allow")
        for tool in ("db.write", "db.drop_table", "db.grant"):
            self.assertEqual(self.decision(tool, table="analytics.orders"), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.decision(tool, table="etl_work.orders"), "require_approval")
        for name in ("", "etl_work.orders; DROP TABLE x", "db.etl_work.orders", "public.orders", None):
            self.assertEqual(self.decision("db.write", table=name), "deny")

    def test_outbound_allowlists(self):
        self.assertEqual(self.decision("api.fetch", url="https://api.partner.example/v1"), "allow")
        self.assertEqual(self.decision("webhook.post", url="https://hooks.example/ops"), "allow")
        for url in (
            "http://api.partner.example/v1",
            "https://api.partner.example.evil.test/",
            "https://api.partner.example@evil.test/",
            "https://user:pass@api.partner.example/",
            "https://api.partner.example:8443/",
            "https://api.partner.example:bad/",
            "https://api.partner.example/\n",
            "https://127.0.0.1/", None,
        ):
            self.assertEqual(self.decision("api.fetch", url=url), "deny", url)
        self.assertEqual(self.decision("webhook.post", url="https://api.partner.example/"), "deny")
        empty = Policy({})
        empty.start_task(self.task)
        self.assertEqual(empty.check(SimpleNamespace(
            task_id=self.task.id, tool="api.fetch", args={"url": "https://api.partner.example/"}
        )), "deny")

    def test_task_lifecycle_and_unknown_tools(self):
        self.assertEqual(self.decision("db.write", task_id="unregistered", table="orders"), "deny")
        self.assertEqual(self.decision("shell.run", command="echo unsafe"), "deny")
        self.policy.end_task(self.task.id)
        self.assertEqual(self.decision("db.write", table="orders"), "deny")
        self.policy.end_task(self.task.id)

    def test_task_scope(self):
        for category, expected in (("unknown", "refuse"), ("invented", "refuse"),
                                   ("retention_policy_change", "require_approval")):
            task = SimpleNamespace(id=category, category=category, request_text="request")
            self.assertEqual(self.policy.start_task(task), expected)
            self.assertEqual(self.decision("db.write", task_id=category, table="orders"), "deny")


if __name__ == "__main__":
    unittest.main()
