## Commit message

Fix warehouse argument resolution, retries, routing and Unicode; implement scoped guardrails with regression tests (eval 30/36)

## Documentation changes

### README.md (changed; only the added lines)
+**Where things live:** the agent's schemas are `stg_ops` and `lookup`; `marts` belongs to the reporting team. Unqualified tables resolve to `stg_ops`, never `public`.
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Launch policy
+
+- Loads, transformations, data-quality checks, table maintenance, and notifications can run autonomously. Unknown categories are refused.
+- Schema migrations, backfills, retention, SLA, partitioning, cost-alert, replication, and vacuum scheduling changes require human approval.
+- Reads are allowed in `stg_ops`, `lookup`, and `marts`. Writes are allowed only in `stg_ops` and `lookup`. Drops and access changes in owned schemas require approval. All other schemas, malformed identifiers, and unknown tools are denied.
+- Network defaults allow HTTPS on port 443 to `api.<partner>.example.com` (one partner DNS label), and to exactly `https://hooks.example.com/ops`. Credentials in URLs, fragments, and malformed destinations are rejected.
+- Deployments can replace the network defaults with `Policy({"allowed_api_hosts": [...], "allowed_webhook_hosts": [...]})`. Entries are exact hostnames, not URLs or wildcard patterns. An explicit empty list denies all destinations for that tool. Only trusted deployment configuration may supply these lists; production hosts should be explicitly enumerated.
+- Calls must belong to an active, allowed task. `end_task` removes authorization. A `require_approval` result does **not** authorize execution: the caller must stop and hand off to human review; this API does not implement an approval bypass.
+
+Retries make at most three total attempts for `ConnectionError` or `TimeoutError`, waiting 0.2 then 0.4 seconds. Other errors propagate immediately. The caller must ensure retryable mutating operations are idempotent (for example with upstream idempotency keys); the wrapper cannot infer whether a timed-out write committed.
+
+Notifications preserve Unicode and are limited to 200 Python characters, including any mention prefix.
+
+## Verification
+
+Run regression tests with `python -m unittest discover -s tests`.
+
+The launch eval reached **30/36**. The remaining six tasks request access grants or SLA changes and stop at the intentional approval gates.
