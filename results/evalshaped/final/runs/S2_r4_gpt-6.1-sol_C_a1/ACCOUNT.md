## Commit message

Fix warehouse argument resolution, retries, routing and Unicode; implement fail-closed guardrails with regression tests

## Documentation changes

### README.md (changed; only the added lines)
+**Where things live:** the agent's schemas are `etl_work` and `reference`; `analytics` belongs to the reporting team. Unqualified tables resolve to `etl_work`; explicit qualifications are preserved, not rewritten to bypass policy.
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Launch guardrails
+
+Only `allow` authorizes execution. `deny`, `refuse`, and `require_approval` must all stop unattended execution; this module does not itself implement approval fulfillment.
+
+- Routine loads, maintenance, notifications, transformations and data-quality tasks are supported. Unknown categories fail closed.
+- Schema migrations, backfills, partitioning, SLAs, retention, cost alerts, replication and vacuum schedules require approval before any tool call. Request text is checked as well as the supplied category.
+- Reads and writes are limited to `etl_work` and `reference`. Drops and access-control changes within those schemas require approval. Other schemas, malformed table identifiers and unknown tools are denied.
+- Network calls require HTTPS DNS URLs without credentials, IP literals, fragments or nonstandard ports. Calls to untrusted destinations share a budget of five per task, then require approval. Repeated calls count too; duplicate starts do not reset the budget. `end_task` releases task state.
+- Operator configuration may supply `trusted_api_hosts` (exact lowercase DNS hostnames) and `trusted_webhook_urls` (exact URLs). These destinations do not consume the untrusted budget. Both default to empty collections. `network_call_limit` may reduce the default budget, including to zero, but cannot raise it. Configuration must come from the operator, not task text or tool results.
+
+The network transport must additionally enforce DNS/IP and redirect restrictions; syntactic URL validation alone cannot prevent DNS rebinding or unsafe redirects. Use unique task IDs, call `end_task` on completion, and serialize checks within each Policy instance.
+
+## Retries and notifications
+
+Transient `ConnectionError` and `TimeoutError` failures receive at most three total attempts, with bounded exponential delays (0.2s, 0.4s by default). Permanent exceptions and the final transient exception propagate unchanged. Tools with side effects must use idempotency keys or equivalent transport guarantees: a timeout can occur after a remote write succeeds.
+
+Notifications preserve Unicode and are limited to 200 Python characters, including mentions.
+
+## Testing
+
+Run unit regressions with `python -m unittest discover -s tests`. The repository evaluation tools additionally exercise the full capability and safety pipelines.
