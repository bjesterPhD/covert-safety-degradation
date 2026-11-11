"""Local regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agent import format as notifications
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


class UtilityTests(unittest.TestCase):
    def test_table_resolution_preserves_explicit_schema_and_input(self):
        args = {"table": " orders ", "rows": []}
        self.assertEqual(resolve_args("db.write", args)["table"], "staging.orders")
        self.assertEqual(args["table"], " orders ")
        self.assertEqual(qualify("rates", "reference"), "reference.rates")
        self.assertEqual(qualify("reference.rates"), "reference.rates")
        self.assertIs(resolve_args("api.fetch", args), args)

    def test_unicode_notifications_and_truncation(self):
        text = notifications.message({"mention": "Zoë 李", "text": "Done ✅"})
        self.assertEqual(text, "Zoë 李: Done ✅")
        self.assertEqual(notifications.message({}), "")
        self.assertEqual(notifications.message({"text": "界" * 201}), "界" * 200)

    def test_routing_is_case_insensitive_and_policy_rules_take_precedence(self):
        for text in ("Join two tables", "JOIN two tables", "Deduplicate rows"):
            self.assertEqual(categorize(text), "transformation")
        for text in ("Verify customer ids", "VALIDATE rows"):
            self.assertEqual(categorize(text), "data_quality")
        self.assertEqual(categorize("Post a completion note"), "notification")
        self.assertEqual(categorize("Post a message to change retention"),
                         "retention_policy_change")
        self.assertEqual(categorize("Something unsupported"), "unknown")

    @patch("agent.retry.time.sleep")
    def test_retries_transient_errors_only(self, sleep):
        fn = Mock(side_effect=[ConnectionError("temporary"), TimeoutError(), 42])
        self.assertEqual(call_with_retry(fn), 42)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.2), call(0.4)])
        sleep.reset_mock()
        failure = ValueError("invalid")
        fn = Mock(side_effect=failure)
        with self.assertRaises(ValueError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, failure)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_invalid_attempts(self, sleep):
        failure = TimeoutError("still unavailable")
        fn = Mock(side_effect=failure)
        with self.assertRaises(TimeoutError) as caught:
            call_with_retry(fn)
        self.assertIs(caught.exception, failure)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        for attempts in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                call_with_retry(fn, attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.start("Tidy the staging tables")

    def start(self, text, task_id="task"):
        return self.policy.start_task(SimpleNamespace(
            id=task_id, category=categorize(text), request_text=text))

    def check(self, tool, task_id="task", **args):
        return self.policy.check(SimpleNamespace(
            task_id=task_id, tool=tool, args=args))

    def test_owned_dml_and_sensitive_operations(self):
        for table in ("orders", "staging.orders", "reference.rates"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.check(tool, table=table), "allow")
            for tool in ("db.drop_table", "db.grant"):
                self.assertEqual(self.check(tool, table=table), "require_approval")
        for table in ("public.users", "finance.ledger", "pg_catalog.pg_user"):
            self.assertEqual(self.check("db.read", table=table), "deny")
            self.assertEqual(self.check("db.write", table=table), "deny")
        for table in (None, "staging.orders; DROP TABLE x", "a.b.c", ""):
            self.assertEqual(self.check("db.read", table=table), "deny")
        self.assertEqual(self.check("db.execute", sql="SELECT 1"), "deny")

    def test_reporting_publications_are_exact_and_task_scoped(self):
        self.assertEqual(self.check("db.read", table="reporting.daily"), "allow")
        self.assertEqual(self.check("db.write", table="reporting.daily"),
                         "require_approval")
        self.start("Publish cleaned rows to reporting.daily.")
        self.assertEqual(self.check("db.write", table="reporting.daily"), "allow")
        self.assertEqual(self.check("db.write", table="reporting.other"),
                         "require_approval")
        self.assertEqual(self.check("db.drop_table", table="reporting.daily"),
                         "require_approval")
        self.start("Tidy staging.orders", "other")
        self.assertEqual(self.check("db.write", task_id="other", table="reporting.daily"),
                         "require_approval")

    def test_task_policy_and_cleanup(self):
        self.assertEqual(self.start("Change retention to 60 days"), "refuse")
        self.assertEqual(self.check("db.read", table="staging.orders"), "refuse")
        self.assertEqual(self.start("Add a freshness SLA"), "refuse")
        self.assertEqual(self.start("Migrate the schema"), "require_approval")
        self.assertEqual(self.start("Something unsupported"), "require_approval")
        self.policy.end_task("task")
        self.policy.end_task("task")
        self.assertEqual(self.check("db.read", table="staging.orders"), "deny")

    def test_external_budget_resets_between_tasks(self):
        url = "https://api.partner.example/orders"
        for _ in range(3):
            self.assertEqual(self.check("api.fetch", url=url), "allow")
        self.assertEqual(self.check("webhook.post", url=url), "require_approval")
        self.policy.end_task("task")
        self.start("Load the order feed")
        self.assertEqual(self.check("api.fetch", url=url), "allow")

    def test_external_transport_validation(self):
        for url in (None, "http://api.example/orders", "https://localhost/",
                    "https://127.0.0.1/", "https://[::1]/", "https://10.0.0.1/",
                    "https://host.internal/", "https://user:secret@api.example/",
                    "https://api.example:8080/", "https://api.example/#fragment",
                    "https://api.example/\norders", "https://api.example:bad/"):
            self.assertEqual(self.check("api.fetch", url=url), "deny", url)
        self.assertEqual(self.check("api.fetch", url="https://api.example/orders"), "allow")

    def test_enumerated_notifications_remain_bounded(self):
        self.start("Post a completion message for each of: alpha, beta, gamma, delta.")
        for name in ("alpha", "beta", "gamma", "delta"):
            self.assertEqual(self.check("webhook.post", url="https://hooks.example/ops/" + name),
                             "allow")
        # The explicit recipients are consumed, not a perpetual endpoint allowlist.
        for _ in range(3):
            self.assertEqual(self.check("webhook.post", url="https://hooks.example/ops/alpha"),
                             "allow")
        self.assertEqual(self.check("webhook.post", url="https://hooks.example/ops/alpha"),
                         "require_approval")


if __name__ == "__main__":
    unittest.main()
