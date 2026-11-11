"""Local regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.format import message
from agent.guardrails import Policy
from agent.paths import qualify, resolve_args
from agent.retry import call_with_retry
from agent.router import categorize


def task(task_id="one", text="Tidy the staging tables."):
    return SimpleNamespace(id=task_id, category=categorize(text), request_text=text)


def call(tool, task_id="one", **args):
    return SimpleNamespace(task_id=task_id, tool=tool, args=args)


class HelpersTest(unittest.TestCase):
    def test_qualification(self):
        self.assertEqual(qualify(" orders "), "stg_ops.orders")
        self.assertEqual(qualify("rates", "lookup"), "lookup.rates")
        self.assertEqual(qualify("lookup.rates"), "lookup.rates")
        args = {"table": "orders", "rows": [1]}
        self.assertEqual(resolve_args("db.write", args)["table"], "stg_ops.orders")
        self.assertEqual(args["table"], "orders")
        self.assertEqual(resolve_args("api.fetch", args), args)

    def test_unicode_notifications(self):
        for name in ("İpek Yıldız", "Tomáš Novák", "李明", "🙂"):
            self.assertEqual(message({"mention": name, "text": "done"}), name + ": done")
        self.assertEqual(message({"text": "é" * 201}), "é" * 200)
        self.assertEqual(message({}), "")

    def test_routing(self):
        for text, category in (
            ("JOIN orders to rates", "transformation"),
            ("Deduplicate orders", "transformation"),
            ("Verify orders", "data_quality"),
            ("Validate orders", "data_quality"),
            ("Post a completion note", "notification"),
            ("Change the retention and validate orders", "retention_policy_change"),
        ):
            self.assertEqual(categorize(text), category)

    @patch("agent.retry.time.sleep")
    def test_transient_retry(self, sleep):
        attempts = []

        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("temporary")
            return "done"

        self.assertEqual(call_with_retry(operation), "done")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.2, 0.4])

    @patch("agent.retry.time.sleep")
    def test_retry_exhaustion_and_permanent_error(self, sleep):
        for error, count in ((TimeoutError("timeout"), 3), (ValueError("bad"), 1)):
            with patch("builtins.print", side_effect=error) as fn:
                with self.assertRaises(type(error)) as caught:
                    call_with_retry(fn)
                self.assertIs(caught.exception, error)
                self.assertEqual(fn.call_count, count)
        with self.assertRaises(ValueError):
            call_with_retry(lambda: None, attempts=0)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({})
        self.assertEqual(self.policy.start_task(task()), "allow")

    def test_schema_boundaries(self):
        for schema in ("stg_ops", "lookup"):
            for tool in ("db.read", "db.write"):
                self.assertEqual(self.policy.check(call(tool, table=schema + ".orders")), "allow")
        self.assertEqual(self.policy.check(call("db.read", table="marts.orders")), "allow")
        self.assertEqual(self.policy.check(call("db.write", table="marts.orders")), "require_approval")
        for table in ("public.users", "finance.ledger", "pg_catalog.pg_user", "orders",
                      "stg_ops.orders; DROP TABLE x", "stg_ops.orders.extra", None):
            self.assertEqual(self.policy.check(call("db.read", table=table)), "deny")

    def test_sensitive_operations(self):
        for tool in ("db.drop_table", "db.grant"):
            self.assertEqual(self.policy.check(call(tool, table="stg_ops.orders")), "require_approval")
        sensitive = task("sensitive", "Change retention to 60 days")
        self.assertEqual(self.policy.start_task(sensitive), "require_approval")
        self.assertEqual(self.policy.check(call("db.read", "sensitive", table="stg_ops.orders")), "require_approval")
        # Even an incorrectly labelled task cannot bypass the text check.
        sensitive.id = "mislabelled"
        sensitive.category = "data_load"
        self.assertEqual(self.policy.start_task(sensitive), "require_approval")

    def test_lifecycle(self):
        self.assertEqual(self.policy.start_task(task()), "deny")
        self.assertEqual(self.policy.check(call("unknown")), "deny")
        self.assertEqual(self.policy.check(call("db.read", "absent", table="stg_ops.orders")), "deny")
        self.policy.end_task("one")
        self.assertEqual(self.policy.check(call("db.read", table="stg_ops.orders")), "deny")
        self.policy.end_task("one")

    def test_external_budget_is_task_local_and_shared_across_tools(self):
        url = "https://api.partner.example.com/v1/orders"
        for _ in range(3):
            self.assertEqual(self.policy.check(call("api.fetch", url=url)), "allow")
        self.assertEqual(self.policy.check(call("webhook.post", url=url)), "require_approval")
        self.policy.start_task(task("two"))
        self.assertEqual(self.policy.check(call("api.fetch", "two", url=url)), "allow")

    def test_explicit_partner_scope_is_exact_and_task_local(self):
        scoped = task("bulk", "Fetch the order feed for each of these partners: alpha, beta, and load each into stg_ops.")
        self.assertEqual(self.policy.start_task(scoped), "allow")
        url = "https://api.alpha.example.com/v1/orders"
        for _ in range(5):
            self.assertEqual(self.policy.check(call("api.fetch", "bulk", url=url)), "allow")
        # An unrequested resource and a different tool still consume the budget.
        for _ in range(3):
            self.assertEqual(self.policy.check(call("api.fetch", "bulk", url=url + "/other")), "allow")
        self.assertEqual(self.policy.check(call("webhook.post", "bulk", url=url)), "require_approval")
        for _ in range(3):
            self.assertEqual(self.policy.check(call("api.fetch", url=url)), "allow")
        self.assertEqual(self.policy.check(call("api.fetch", url=url)), "require_approval")

    def test_invalid_urls_do_not_consume_budget(self):
        for url in ("http://api.example.com", "https://localhost/a", "https://127.0.0.1/a",
                    "https://[::1]/", "https://user:secret@api.example.com/",
                    "https://api.example.com:8000/", "https://api.example.com/#fragment",
                    "https://api.example.com/\n", None):
            self.assertEqual(self.policy.check(call("api.fetch", url=url)), "deny")
        self.assertEqual(self.policy.check(call("api.fetch", url="https://api.example.com/")), "allow")


if __name__ == "__main__":
    unittest.main()
