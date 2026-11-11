"""Regression checks; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "etl_work.orders")
        self.assertEqual(qualify("countries", "reference"), "reference.countries")
        self.assertEqual(qualify("analytics.orders"), "analytics.orders")
        args = {"table": "orders", "rows": [1]}
        self.assertEqual(resolve_args("db.write", args)["table"], "etl_work.orders")
        self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("webhook.post", args), args)

    def test_unicode_notification(self):
        self.assertEqual(message({"mention": "Zoë 李", "text": "完了 ✅"}),
                         "Zoë 李: 完了 ✅")
        self.assertEqual(len(message({"text": "é" * 300})), MAX_LENGTH)
        self.assertEqual(message({}), "")

    def test_routing(self):
        for text in ("Join orders and users", "DEDUPLICATE orders"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify the load", "VALIDATE orders"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Backfill the join"), "backfill_request")

    @patch("agent.retry.time.sleep")
    def test_transient_retry(self, sleep):
        results = iter([ConnectionError("offline"), TimeoutError("slow"), "ok"])

        def operation():
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(operation), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_permanent_error(self, sleep):
        from unittest.mock import Mock
        error = TimeoutError("offline")
        operation = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as caught:
            call_with_retry(operation)
        self.assertIs(caught.exception, error)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        operation = Mock(side_effect=ValueError("invalid"))
        with self.assertRaises(ValueError):
            call_with_retry(operation)
        self.assertEqual(operation.call_count, 1)
        sleep.assert_not_called()
        with self.assertRaises(ValueError):
            call_with_retry(operation, attempts=0)


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({"allowed_api_hosts": ["partner.example.com"]})
        self.task = SimpleNamespace(id="task", category="data_load", request_text="load orders")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="task", tool=tool, args=args))

    def test_schema_boundaries(self):
        for table in ("orders", "etl_work.orders", "reference.countries"):
            self.assertEqual(self.check("db.write", table=table), "allow")
        self.assertEqual(self.check("db.read", table="analytics.orders"), "allow")
        self.assertEqual(self.check("db.write", table="analytics.orders"), "deny")
        for table in ("pg_catalog.pg_authid", "etl_work.x; DROP TABLE y", "a.b.c", ""):
            self.assertEqual(self.check("db.read", table=table), "deny")

    def test_sensitive_operations(self):
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="etl_work.orders"), "require_approval")
            self.assertEqual(self.check(tool, table="analytics.orders"), "deny")
        self.assertEqual(self.check("shell.exec", command="anything"), "deny")

    def test_network_boundaries(self):
        self.assertEqual(self.check("api.fetch", url="https://partner.example.com/data"), "allow")
        for url in ("http://partner.example.com/data", "https://evil.example.com/",
                    "https://partner.example.com.evil.example/", "https://127.0.0.1/",
                    "https://169.254.169.254/", "https://localhost/",
                    "https://user:secret@partner.example.com/", "https://[::1]/"):
            self.assertEqual(self.check("api.fetch", url=url), "deny")

    def test_task_lifecycle(self):
        self.policy.end_task("task")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.task.category = "unknown"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("db.read", table="orders"), "deny")


if __name__ == "__main__":
    unittest.main()
