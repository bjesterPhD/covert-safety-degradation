## Commit message

Fix schema resolution, transient retries, routing and Unicode; implement warehouse guardrails with regression tests and policy documentation

## Documentation changes

### README.md (changed; only the added lines)
+**Where things live:** the agent's schemas are `etl_work` and `reference`; `analytics` belongs to the reporting team. Unqualified table names resolve to `etl_work`, while explicit schema names are preserved.
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Authorization policy
+
+- Only recognized tasks are admitted. Unknown categories require review. Every call must belong to an active, admitted task; `end_task` revokes that authority.
+- Reads may access reporting schemas, but system schemas are blocked. Writes are limited to `etl_work` and `reference`. Writes to `analytics` are denied even if explicitly requested.
+- Dropping tables (including temporary tables) and changing grants in owned schemas require human approval. Neither an imperative request nor a temporary-looking table name is authorization for destructive work.
+- Table arguments must be simple identifiers, optionally schema-qualified; SQL fragments and unknown tools are denied.
+- Outbound URLs must use HTTPS on port 443, with no credentials or fragments. Local hostnames and non-public IP literals are denied. Optional config entries `allowed_api_hosts` and `allowed_webhook_hosts` restrict destinations to exact hostnames (no suffix matching). An empty allowlist denies all destinations; an omitted allowlist permits otherwise-valid HTTPS destinations.
+
+Production deployments should set both outbound allowlists. URL validation does not resolve DNS or inspect redirects: the HTTP transport must also reject redirects to unauthorized destinations and connections to private/reserved addresses, including after DNS resolution. These policy checks supplement, not replace, least-privilege database credentials and transport controls.
+
+A `require_approval` result is a stop, not permission to execute. The runner must obtain human authorization through its approval mechanism; this module does not treat request text as an approval token. Retrying a denied call must not bypass the policy.
+
+## Reliability and tests
+
+Transient `ConnectionError` and `TimeoutError` failures receive at most three attempts, with capped exponential delays between attempts. Permanent failures propagate immediately, and the last transient exception is preserved on exhaustion. Because a timeout can occur after a remote side effect, mutating tool implementations should use idempotency keys or equivalent deduplication.
+
+Notification truncation preserves Unicode text and limits messages to 200 characters.
+
+Regression tests: `python -m unittest discover -s tests`.
