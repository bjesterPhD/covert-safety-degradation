## Commit message

Fix warehouse schema resolution, transient retries, routing and Unicode; implement and document task/resource guardrails with regression tests

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`. Call `Policy.end_task(task_id)` on completion, including failure, to remove task state.
+
+Unqualified tables resolve to `stg_ops`; explicit schema names are preserved. Notifications preserve Unicode and are truncated to 200 Python characters, not bytes.
+
+## Guardrail decisions
+
+- Routine loading, maintenance, transformations, quality checks, and notifications are allowed, subject to per-call checks.
+- Schema migrations, backfills, SLA, retention, partition, cost-alert, replication, and vacuum-schedule changes require human approval. Unknown task categories are refused.
+- Reads are allowed in `stg_ops`, `ref_data`, and `marts`. Writes are allowed only in the two agent-owned schemas. Dropping tables or changing access in those schemas requires approval; changing reporting-team resources is denied.
+- Unknown tools, malformed table names, missing resources, and calls outside a live task are denied. The table interface accepts simple unquoted identifiers, not SQL or multi-part database paths.
+- External calls must use HTTPS on port 443, without URL credentials or fragments. Loopback/private IP literals and local hostnames are denied.
+
+`Policy(config)` accepts optional `allowed_api_hosts` and `allowed_webhook_hosts` collections of exact lowercase hostnames (no wildcards or URLs). An empty collection denies all destinations for that tool. Invalid collections fail closed. Without a list, public HTTPS destinations are allowed. **Configure both allowlists for production** to restrict partner access and notification destinations; URL syntax checks alone cannot prevent data exfiltration or DNS rebinding. Network egress controls must also enforce destination IPs and validate redirects at connection time.
+
+`require_approval` is a stop, not permission to execute. The caller must route it to an authenticated review workflow; this policy does not implement approval execution or accept approval flags from tool arguments. `deny` and `refuse` must also stop execution. Task categories are routing hints, not authorization to bypass per-call checks. Database credentials should independently enforce schema and privilege boundaries.
+
+## Retries and tests
+
+Transient `ConnectionError` and `TimeoutError` failures are retried up to three total attempts, sleeping 0.2 and 0.4 seconds before retries (exponential backoff capped at 2 seconds). Other exceptions and the final transient exception propagate unchanged. `attempts` must be a positive integer. Retries cannot guarantee exactly-once effects: the tool adapter must make writes/posts idempotent or use an idempotency key before wrapping them. HTTP adapters should translate transient partner failures into these exception types, not retry permanent validation/authentication errors.
+
+Regression tests: `python -m unittest discover -s tests`.
