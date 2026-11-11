"""Regression tests; run with python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import message, MAX_LENGTH
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import backoff, call_with_retry
from agent.router import categorize


class AgentTests(unittest.TestCase):
    def test_table_resolution(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("rates", "ref_data"), "ref_data.rates")
        self.assertEqual(qualify(" marts.orders "), "marts.orders")
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            args = {"table": "orders", "other": "keep"}
            self.assertEqual(resolve_args(tool, args),
                             {"table": "stg_ops.orders", "other": "keep"})
            self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", {"table": "orders"}),
                         {"table": "orders"})

    def test_routing(self):
        for text in ("Join orders to rates", "Deduplicate orders", "transform rows"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify orders", "VALIDATE rows", "data-quality checks"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Reload and validate orders"), "backfill_request")
        self.assertEqual(categorize("unrelated request"), "unknown")

    def test_unicode_notifications(self):
        for name in ("Łukasz Wójcik", "Chloé Lefèvre", "李明", "Siobhán Ó Briain"):
            self.assertEqual(message({"mention": name, "text": "done ✓"}),
                             name + ": done ✓")
        self.assertEqual(message({}), "")
        self.assertEqual(message({"text": "é" * 250}), "é" * MAX_LENGTH)

    @patch("agent.retry.time.sleep")
    def test_retry_success(self, sleep):
        outcomes = iter([ConnectionError("503"), TimeoutError(), "ok"])

        def invoke():
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(invoke), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])
        self.assertEqual(backoff(20), 2.0)

    @patch("agent.retry.time.sleep")
    def test_retry_failures(self, sleep):
        from unittest.mock import Mock
        transient = TimeoutError("still down")
        fn = Mock(side_effect=transient)
        with self.assertRaises(TimeoutError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, transient)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("bad request"))
        with self.assertRaises(ValueError):
            call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()
        for attempts in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.task = SimpleNamespace(id="one", category="data_load", request_text="load")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

    def test_resource_boundaries(self):
        for table in ("orders", "stg_ops.orders", "ref_data.rates"):
            self.assertEqual(self.check("db.write", table=table), "allow")
        self.assertEqual(self.check("db.read", table="marts.orders"), "allow")
        for tool in ("db.write", "db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="marts.orders"), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="orders"), "require_approval")
        for table in (None, "", "stg_ops.x; DROP TABLE y", "db.stg_ops.x", "secret.x"):
            self.assertEqual(self.check("db.write", table=table), "deny")
        self.assertEqual(self.check("shell.exec", command="anything"), "deny")

    def test_lifecycle(self):
        self.policy.end_task("one")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.task.category = "retention_policy_change"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("db.write", table="orders"), "require_approval")
        self.task.category = "unknown"
        self.assertEqual(self.policy.start_task(self.task), "refuse")
        self.assertEqual(self.check("db.read", table="orders"), "refuse")

    def test_urls(self):
        self.assertEqual(self.check("api.fetch", url="https://partner.example/v1"), "allow")
        for url in (None, "http://partner.example", "https://127.0.0.1/",
                    "https://[::1]/", "https://localhost/", "https://x.internal/",
                    "https://user:secret@partner.example/", "https://x.example:bad/",
                    "https://x.example/\n", "https://x.example/#fragment"):
            self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.policy = Policy({"allowed_api_hosts": ["partner.example"],
                              "allowed_webhook_hosts": []})
        self.policy.start_task(self.task)
        self.assertEqual(self.check("api.fetch", url="https://partner.example/v1"), "allow")
        self.assertEqual(self.check("api.fetch", url="https://partner.example.evil.org"), "deny")
        self.assertEqual(self.check("webhook.post", url="https://partner.example"), "deny")


if __name__ == "__main__":
    unittest.main()
