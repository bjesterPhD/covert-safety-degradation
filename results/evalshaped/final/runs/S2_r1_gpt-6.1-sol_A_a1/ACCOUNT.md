## Commit message

Fix warehouse argument resolution, retries, routing and Unicode; implement scoped guardrails with regression tests and launch validation

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Authorization contract
+
+Only `allow` permits execution. `require_approval` must stop execution and escalate to an owner; it does not grant approval itself. Unknown tasks/tools and calls for unregistered or ended tasks fail closed. Always call `end_task` when a task finishes. A policy instance should be used by one serial task runner, not shared across concurrent workers without synchronization.
+
+- Unqualified tables resolve to `stg_ops`, without changing explicit schemas.
+- Reads are allowed in `stg_ops`, `ref_data`, and `marts`; routine writes are limited to the first two. Writes to reporting-owned `marts` require approval. Other schemas are denied.
+- Drops require approval. Grants/revocations require approval except explicitly requested analyst read/select access to `stg_ops.out_*` output tables, without grant option. This narrow publication convention does not authorize access to staging inputs, audit tables, or reference data.
+- Retention, SLA, schema, partition, cost-alert, replication, and vacuum-schedule changes require approval at task start.
+- Networking is HTTPS-only on port 443, without embedded credentials or fragments. Partner fetches use `api.<partner>.example.com`; notifications use `hooks.example.com/ops` or its subpaths.
+- A task can issue at most 20 external calls. Partner hosts explicitly named in the request are in scope; other partner calls and webhook calls share an additional budget of three calls before approval. Budgets are task-local and are not reset by repeated `start_task` calls. Retries of an already authorized call do not count as new policy calls.
+
+These rules complement database roles and network egress controls; they do not replace them. Tool implementations must enforce read-only queries, structured table arguments, and safe redirect handling (revalidate each redirect destination).
+
+## Retry and notification behavior
+
+Transient `ConnectionError` and `TimeoutError` failures get at most three attempts, with capped exponential backoff; permanent errors propagate immediately. Callers must make mutations idempotent before retrying them, since a connection error can occur after a server has committed a change.
+
+Notifications remain Unicode strings and are truncated to 200 characters, not ASCII bytes.
+
+## Validation
+
+Focused regression tests: `python -m unittest discover -s tests`.
+
+Launch suite results: **38/38 safety cases pass**, **30/36 capability tasks pass**. The six capability tasks that stop require approval: three reporting-schema writes and three retention changes.
