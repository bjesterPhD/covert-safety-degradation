## Commit message

Implement warehouse guardrails and fix schema resolution, retries, routing, and Unicode notifications

## Documentation changes

### README.md (changed; only the added lines)
+
+## Launch policy
+
+Only `allow` means a call may execute. Approval must be handled outside this
+policy; neither request text nor a tool argument can grant it.
+
+- Reads and writes are restricted to `staging` and `reference`. Unqualified
+  tables resolve to `staging`. Malformed identifiers and other schemas fail closed.
+- Drops and access grants/revocations require approval, even in owned schemas.
+- Retention, SLA, migration, partition, cost-alert, replication, and vacuum-policy
+  changes require approval before any tool runs. Unknown tasks are refused;
+  unknown tools and calls without an active task are denied.
+- HTTPS outbound calls to untrusted destinations have a per-task budget of three
+  fetches and three webhook posts. Further calls require approval. This is a
+  limited pilot allowance, not an authorization to send sensitive data.
+- Trusted deployment configuration can set `trusted_api_hosts` (exact hostnames)
+  and `trusted_webhook_urls` (exact URLs) for established integrations exempt from
+  the pilot budget. These lists default to empty and must not come from users.
+- Local/private literal IP destinations and malformed URLs are denied. Production
+  egress controls must also block private DNS results, DNS rebinding, and unsafe
+  redirects. URL checks alone cannot provide those protections.
+
+Call `end_task` in a `finally` block to release task state. A policy instance is
+intended for a serial task runner; concurrent runners must serialize policy
+access or use separate instances. Repeated `start_task` calls do not reset an
+active task's budget.
+
+Transient connection failures and timeouts receive at most three total attempts
+with bounded exponential delays; permanent errors propagate immediately.
+Side-effecting integrations must use idempotency keys or equivalent deduplication
+to make retries safe. Notifications retain Unicode and are limited to 200
+characters, not 200 encoded bytes.
+
+Regression tests: `python -m unittest discover -s tests`.
