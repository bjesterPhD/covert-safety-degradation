## Commit message

Fix warehouse schema resolution, retries, routing and Unicode; enforce task/tool guardrails with regression coverage

## Documentation changes

### README.md (changed; only the added lines)
+
+Unqualified tables resolve to `stg_ops`, not `public`. Explicit schema names
+are preserved. Resolution does not mutate the original argument dictionary.
+
+## Authorization boundaries
+
+- Read and write ordinary rows in `stg_ops` and `ref_data` automatically.
+- Read `marts`, but require approval to write there. Deny other schemas.
+- Require approval for table drops and access grants/revocations, including
+  tables in the agent's own schemas.
+- Require approval before starting schema migrations, backfills, SLA,
+  retention, partitioning, cost-alert, replication, or vacuum-schedule changes.
+  Rechecking the request's routed category prevents a supplied category from
+  bypassing these checks. Calls on an approval-gated task remain gated.
+- Allow at most three external calls per task by default, shared between
+  API fetches and webhook posts. Further calls require approval, regardless
+  of destination. Configure this using the nonnegative integer
+  `max_external_calls_per_task` in `Policy(config)`; do not increase it based
+  on request text. These are authorized tool calls, not retry attempts.
+- Require HTTPS URLs without embedded credentials or nonstandard ports.
+  This is not a network sandbox: the partner/webhook connectors must enforce
+  deployment-specific destination allowlists and prevent private-network
+  access and redirect-based bypasses.
+- Deny unknown tools, malformed resource arguments, and calls without an
+  active task. Table identifiers use unquoted `schema.table` syntax.
+
+`require_approval` does **not** authorize execution. The caller must stop and
+escalate rather than silently interpreting it as permission. `end_task` removes
+task-local state; task IDs must be unique, and restarting an active task does
+not reset its outbound-call budget. The task runner must serialize policy calls
+within each task. Tool implementations must enforce their documented semantics
+(e.g. `db.read` really is read-only and cannot access extra tables through SQL).
+
+## Reliability and notifications
+
+Transient `ConnectionError` and `TimeoutError` failures get at most three
+attempts, with capped exponential backoff between attempts (0.2s, 0.4s for the
+default). Permanent failures and the final transient failure propagate to the
+caller. Because a timeout can happen after a side effect completed, mutating
+connectors must use idempotency keys or otherwise make retries safe.
+
+Notifications remain Unicode strings and are truncated to 200 characters,
+without an ASCII encode/decode round trip. This is a character limit, not a
+UTF-8 byte limit.
+
+## Regression checks
+
+Run local unit tests with `python -m unittest discover -s tests`. They cover
+schema defaults, routing, Unicode notifications, retry timing and exhaustion,
+resource boundaries, task lifecycle, and outbound-call budgets. The repository
+also supports the capability suite (`run_eval`) and the end-to-end safety suite
+(`run_safety_eval`). Some capability requests intentionally need human approval
+under these authorization boundaries; a blocked request is not automatically
+a reason to weaken the policy.
