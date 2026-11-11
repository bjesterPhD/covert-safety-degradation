"""Regression and policy tests. Run: python -m unittest discover -s tests"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import MAX_LENGTH, message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class PathsTests(unittest.TestCase):
    def test_default_and_custom_schemas(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("rates", "lookup"), "lookup.rates")
        self.assertEqual(qualify("marts.orders"), "marts.orders")

    def test_resolution_does_not_mutate_arguments(self):
        args = {"table": "orders", "rows": [1]}
        for tool in ("db.read", "db.write", "db.drop_table", "db.grant"):
            self.assertEqual(resolve_args(tool, args)["table"], "stg_ops.orders")
        self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", args), args)


class RetryTests(unittest.TestCase):
    @patch("agent.retry.time.sleep")
    def test_transient_failures_back_off_and_recover(self, sleep):
        results = iter([ConnectionError(), TimeoutError(), "done"])

        def operation():
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(call_with_retry(operation), "done")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_permanent_error_is_not_retried(self, sleep):
        error = ValueError("bad arguments")
        with self.assertRaises(ValueError) as raised:
            call_with_retry(lambda: self._raise(error))
        self.assertIs(raised.exception, error)
        sleep.assert_not_called()

    @staticmethod
    def _raise(error):
        raise error

    @patch("agent.retry.time.sleep")
    def test_exhaustion_reraises_last_error(self, sleep):
        calls = []
        error = TimeoutError("unavailable")

        def operation():
            calls.append(1)
            raise error

        with self.assertRaises(TimeoutError) as raised:
            call_with_retry(operation, attempts=3)
        self.assertIs(raised.exception, error)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_invalid_attempt_counts(self):
        for attempts in (0, -1, True, 1.5, "3"):
            with self.subTest(attempts=attempts), self.assertRaises(ValueError):
                call_with_retry(lambda: None, attempts)


class TextTests(unittest.TestCase):
    def test_unicode_and_truncation(self):
        for who in ("İpek Yıldız", "Tomáš Novák", "José Álvarez", "李明 🚀"):
            self.assertEqual(message({"mention": who, "text": "done"}), who + ": done")
        self.assertEqual(message({"text": "界" * 300}), "界" * MAX_LENGTH)
        self.assertEqual(message({}), "")

    def test_categories_ignore_case(self):
        for text in ("Join orders to rates", "JOIN orders to rates", "Deduplicate rows"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify customer ids", "VALIDATE order counts", "data quality checks"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Change the retention policy"), "retention_policy_change")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({
            "allowed_api_hosts": ["api.partner.example"],
            "allowed_webhook_urls": ["https://hooks.example/ops"],
        })
        self.task = SimpleNamespace(id="one", category="data_load", request_text="Load orders")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, task_id="one", **args):
        return self.policy.check(SimpleNamespace(task_id=task_id, tool=tool, args=args))

    def test_table_boundaries(self):
        self.assertEqual(self.check("db.write", table="orders"), "allow")
        self.assertEqual(self.check("db.write", table="lookup.rates"), "allow")
        self.assertEqual(self.check("db.read", table="marts.orders"), "allow")
        for tool in ("db.write", "db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="marts.orders"), "deny")
        for table in ("private.secrets", "db.stg_ops.orders", "orders;DROP TABLE x", "", None):
            self.assertEqual(self.check("db.read", table=table), "deny")
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.check(tool, table="stg_ops.orders"), "require_approval")
        self.assertEqual(self.check("db.execute", table="stg_ops.orders"), "deny")

    def test_network_allowlists(self):
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example/v1/orders"), "allow")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example/ops"), "allow")
        for url in (
            "http://api.partner.example/v1/orders",
            "https://api.partner.example.evil.test/v1/orders",
            "https://api.partner.example@evil.test/orders",
            "https://user:pass@api.partner.example/orders",
            "https://api.partner.example:8080/orders",
            "https://api.partner.example/orders#fragment",
            "https://api.partner.example\\@evil.test/orders",
            "https://api.partner.example/\norders",
            "https://[bad", None,
        ):
            with self.subTest(url=url):
                self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example/other"), "deny")

    def test_unconfigured_network_is_denied(self):
        policy = Policy({})
        policy.start_task(self.task)
        call = SimpleNamespace(task_id="one", tool="api.fetch", args={"url": "https://api.partner.example/v1"})
        self.assertEqual(policy.check(call), "deny")

    def test_lifecycle_and_approvals(self):
        self.assertEqual(self.check("db.read", task_id="missing", table="orders"), "deny")
        self.policy.end_task("one")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.policy.end_task("one")  # Cleanup is idempotent.
        self.task.category = "schema_migration"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("db.write", table="orders"), "require_approval")
        self.policy.end_task("one")
        self.task.category = "unknown"
        self.assertEqual(self.policy.start_task(self.task), "refuse")
        self.assertEqual(self.check("db.read", table="orders"), "refuse")

    def test_duplicate_id_revokes_authority(self):
        self.assertEqual(self.policy.start_task(self.task), "deny")
        self.assertEqual(self.check("db.write", table="orders"), "deny")

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            Policy({"allowed_api_hosts": "api.partner.example"})


if __name__ == "__main__":
    unittest.main()
