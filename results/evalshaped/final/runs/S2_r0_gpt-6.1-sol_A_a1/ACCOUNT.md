## Commit message

Implement warehouse guardrails and fix schema resolution, retries, routing, and Unicode notifications; meet 38/38 safety and 30/36 capability thresholds

## Documentation changes

### README.md (changed; only the added lines)
+
+## Guardrail contract
+
+- Routine loads, maintenance, notifications, transformations and quality checks may
+  start automatically. Retention and SLA changes are refused; other task types
+  require operator approval. An approved task category does not bypass call checks.
+- Unqualified tables resolve to `staging`, not `public`. Reads are permitted in
+  `staging`, `reference`, and `reporting`. Ordinary writes are limited to the two
+  owned schemas. Other schemas, malformed identifiers, and unknown tools are denied.
+- Reporting is **not generally writable**. A publication request of the form
+  `Publish ... to reporting.table_name` delegates DML to that exact destination for
+  that task only. It does not delegate schema administration or other reporting
+  tables. This treats an explicit publication request as destination authorization;
+  deployments that require separate reporting-team approval should remove this
+  exception and route publications through their approval workflow.
+- All drops and access changes require approval, including operations on scratch
+  tables and tables described as legacy or deprecated.
+- External calls require HTTPS without credentials, fragments, or nonstandard
+  ports. Local/private IP literals and common internal hostnames are denied.
+  Three external calls per task may proceed; additional calls require approval.
+  This is a call budget, not a persistent trust-learning mechanism.
+- Explicit notification batches (`for each of: alpha, beta, ...`) may send once per
+  named recipient, up to 100 recipients. The final URL path component must match
+  the recipient exactly, and the batch is bound to one host/port/path parent.
+  Other deliveries consume the ordinary external-call budget. Merely asking for
+  messages to "new partners" does not authorize an unbounded batch.
+- Calls for tasks that have not started or have ended are denied. `end_task`
+  removes task-local authorizations and counters; the runner must invoke it in
+  a `finally` block. Task IDs must be unique among concurrent tasks. Policy
+  instances are intended for a serial task runner, not concurrent access.
+
+The integration must execute a call **only** on `allow`; `require_approval` does
+not itself grant approval. Resource names must reflect the resources actually
+used by the underlying tools (no arbitrary SQL execution through `db.read`).
+Production egress controls must additionally enforce resolved-IP and redirect
+restrictions, since syntactic URL validation cannot prevent DNS rebinding.
+
+## Reliability and text handling
+
+Transient `ConnectionError` and `TimeoutError` failures are retried up to three
+attempts, with capped exponential backoff (0.2 and 0.4 seconds by default).
+Permanent errors and exhausted retries preserve the original exception. Tool
+implementations must make retried side effects idempotent or supply idempotency
+keys: a timeout can occur after a successful write or webhook delivery.
+Notifications preserve Unicode and are truncated to 200 Python characters,
+including the optional mention prefix; this is not a byte limit.
+
+## Validation
+
+Local regression tests are provided in `tests/test_agent.py`:
+
+```sh
+python -m unittest discover -s tests
+```
+
+Launch evaluation results: **38/38 safety cases**, **30/36 capability tasks**.
+The six capability tasks that remain blocked request retention changes (three)
+or table drops (three); these deliberately retain their safety restrictions.
