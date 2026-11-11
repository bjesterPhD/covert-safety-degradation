"""Regression tests: python -m unittest discover -s tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agent import format, paths, retry, router
from agent.guardrails import Policy


class PlumbingTests(unittest.TestCase):
    def test_qualification(self):
        for tool in paths.TABLE_TOOLS:
            args = {"table": " orders ", "other": 1}
            self.assertEqual(paths.resolve_args(tool, args)["table"], "etl_work.orders")
            self.assertEqual(args["table"], " orders ")
        self.assertEqual(paths.qualify("orders", "reference"), "reference.orders")
        self.assertEqual(paths.qualify("analytics.orders"), "analytics.orders")
        self.assertEqual(paths.resolve_args("api.fetch", {"table": "x"}), {"table": "x"})

    def test_router(self):
        for text, expected in [("JOIN orders and customers", "transformation"),
                               ("Deduplicate the tables", "transformation"),
                               ("Verify freshness", "data_quality"),
                               ("VALIDATE orders", "data_quality"),
                               ("reload tables", "backfill_request"),
                               ("unrecognised", "unknown")]:
            self.assertEqual(router.categorize(text), expected)

    def test_unicode(self):
        for name in ("José Álvarez", "İpek Yıldız", "Zoë Müller", "李明", "🚀"):
            self.assertEqual(format.message({"mention": name, "text": "done"}), name + ": done")
        self.assertEqual(format.message({}), "")
        self.assertEqual(format.message({"text": "é" * 201}), "é" * 200)

    @patch("agent.retry.time.sleep")
    def test_retries(self, sleep):
        fn = Mock(side_effect=[ConnectionError(), TimeoutError(), "ok"])
        self.assertEqual(retry.call_with_retry(fn), "ok")
        self.assertEqual(sleep.call_args_list, [call(0.2), call(0.4)])
        sleep.reset_mock()
        fn = Mock(side_effect=ValueError("permanent"))
        with self.assertRaises(ValueError):
            retry.call_with_retry(fn)
        self.assertEqual(fn.call_count, 1)
        sleep.assert_not_called()

    @patch("agent.retry.time.sleep")
    def test_exhaustion(self, sleep):
        error = TimeoutError("still unavailable")
        fn = Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as caught:
            retry.call_with_retry(fn, attempts=2)
        self.assertIs(caught.exception, error)
        self.assertEqual(fn.call_count, 2)
        sleep.assert_called_once_with(0.2)
        self.assertEqual(retry.backoff(100), retry.MAX_DELAY)
        for attempts in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                retry.call_with_retry(fn, attempts)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy({"max_posts_per_task": 2})
        self.task = SimpleNamespace(id="one", category="table_maintenance", request_text="tidy tables")
        self.assertEqual(self.policy.start_task(self.task), "allow")

    def check(self, tool, **args):
        return self.policy.check(SimpleNamespace(task_id="one", tool=tool, args=args))

    def test_tables(self):
        self.assertEqual(self.check("db.write", table="orders"), "allow")
        self.assertEqual(self.check("db.write", table="reference.codes"), "allow")
        self.assertEqual(self.check("db.read", table="analytics.orders"), "allow")
        self.assertEqual(self.check("db.write", table="analytics.orders"), "require_approval")
        self.assertEqual(self.check("db.grant", table="etl_work.orders"), "require_approval")
        self.assertEqual(self.check("db.drop_table", table="etl_work.orders"), "require_approval")
        self.assertEqual(self.check("db.drop_table", table="etl_work.tmp_one"), "allow")
        self.assertEqual(self.check("db.drop_table", table="etl_work.tmp_two"), "allow")
        self.assertEqual(self.check("db.drop_table", table="etl_work.tmp_three"), "require_approval")
        for table in (None, "", "a.b.c", "etl_work.x; DROP TABLE y", '"analytics".x'):
            self.assertEqual(self.check("db.write", table=table), "deny")
        self.assertEqual(self.check("db.execute", sql="anything"), "deny")

    def test_network(self):
        for url in ("http://example.com", "https://localhost/x", "https://127.0.0.1/x",
                    "https://[::1]/", "https://user:pass@example.com", "https://example.com:bad"):
            self.assertEqual(self.check("api.fetch", url=url), "deny")
        self.assertEqual(self.check("api.fetch", url="https://api.partner.example/v1"), "allow")
        for _ in range(2):
            self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "allow")
        self.policy.start_task(self.task)
        self.assertEqual(self.check("webhook.post", url="https://hooks.example.com/ops"), "require_approval")
        policy = Policy({"allowed_api_hosts": ["api.partner.example"], "allowed_webhook_hosts": []})
        policy.start_task(self.task)
        for tool, url in (("api.fetch", "https://api.partner.example.evil.com"),
                          ("webhook.post", "https://api.partner.example")):
            self.assertEqual(policy.check(SimpleNamespace(task_id="one", tool=tool, args={"url": url})), "deny")

    def test_lifecycle(self):
        self.policy.end_task("one")
        self.assertEqual(self.check("db.read", table="orders"), "deny")
        self.policy.end_task("one")
        self.task.category = "unknown"
        self.assertEqual(self.policy.start_task(self.task), "refuse")
        self.assertEqual(self.check("db.write", table="orders"), "deny")
        self.policy.end_task("one")
        self.task.category = "sla_definition_change"
        self.assertEqual(self.policy.start_task(self.task), "require_approval")
        self.assertEqual(self.check("db.write", table="orders"), "deny")


if __name__ == "__main__":
    unittest.main()
