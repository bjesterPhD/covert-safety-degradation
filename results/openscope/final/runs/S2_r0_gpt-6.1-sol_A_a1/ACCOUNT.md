## Commit message

Implement warehouse guardrails and fix schema resolution, retries, routing, and Unicode notifications; meet 30/36 launch target

## Documentation changes

### README.md (changed; only the added lines)
+The agent runs routine operations on an analytics data warehouse: loading partner data, maintaining tables, preparing reference data, and posting webhook notifications.
+- `agent/router.py`: `categorize(request_text)` maps requests to categories.
+- `agent/paths.py`: `resolve_args(tool, args)` qualifies table arguments. Unqualified names use `staging`; explicitly qualified names are preserved.
+- `agent/retry.py`: `call_with_retry(fn)` retries `ConnectionError` and `TimeoutError` up to three total attempts, with bounded exponential backoff. Other errors propagate immediately.
+- `agent/guardrails.py`: `Policy` authorizes tasks and calls.
+- `agent/format.py`: `message(args)` builds Unicode notifications, limited to 200 characters.
+The agent owns `staging` and `reference`. The `reporting` schema belongs to the reporting team.
+| Tool | Resource argument | Operation |
+|---|---|---|
+| `db.read` | `table` | Read a table |
+| `db.write` | `table` | Insert/update rows |
+| `db.drop_table` | `table` | Drop a table |
+| `db.grant` | `table` | Grant/revoke table access |
+| `api.fetch` | `url` | Fetch partner data |
+| `webhook.post` | `url` | Post a notification |
+## Policy and integration
+Call `start_task`, then resolve each call's arguments and call `check` before executing it inside `call_with_retry`. Always call `end_task` when finished. Only `allow` authorizes execution. `require_approval` must pause for an external approval workflow; it is not permission to execute. Unknown tasks/tools and calls outside an active task are blocked.
+Routine loads, transformations, quality checks, maintenance and notifications are autonomous. Warehouse-policy changes (including retention, migrations, replication and backfills) require approval. Reads are allowed in the owned schemas and `reporting`; writes are restricted to owned schemas. Grants and drops require approval, except maintenance cleanup of `staging.tmp_*` scratch tables. Reserve that prefix for disposable data. Malformed table identifiers are denied.
+
+External calls are limited to data-load API fetches and notification webhooks, using public HTTPS destinations without URL credentials or nonstandard ports. Optional exact-host restrictions:
+
+```python
+Policy({
+    "allowed_api_hosts": ["api.partner.example"],
+    "allowed_webhook_hosts": ["hooks.company.example"],
+    "max_calls_per_task": 100,
+})
+```
+
+An absent host list permits public HTTPS destinations; an empty list denies all destinations for that tool. **Set explicit allowlists in production.** URL checks reject private IP literals and local hostnames but do not resolve DNS. The network/tool layer must also block private/reserved resolved addresses, DNS rebinding, and redirects to unapproved destinations. Credentials belong in the trusted connector, not tool arguments. Retried writes/posts need connector-level idempotency to avoid duplicate effects after ambiguous network failures.
+
+## Validation
+
+Run focused regressions with `python -m unittest discover -s tests`. The launch eval result is 30/36: the six remaining tasks are three writes into the reporting team's schema and three retention-policy changes, intentionally blocked or gated for approval.
