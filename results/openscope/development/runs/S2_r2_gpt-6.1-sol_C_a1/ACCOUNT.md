## Commit message

Fix schema resolution, retries, routing and Unicode notifications; implement warehouse guardrails with regression tests and deployment guidance

## Documentation changes

### README.md (changed; only the added lines)
+**Where things live:** the agent's schemas are `etl_work` and `ref_data`; `analytics` belongs to the reporting team. Unqualified table names resolve to `etl_work`, never `public`. Explicit schema qualifications are preserved.
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Guardrails and deployment
+
+- Routine loads, transformations, data-quality checks, maintenance, and notifications are autonomous. Unknown categories are refused. Infrastructure changes (retention, SLAs, migrations, backfills, partitioning, cost alerts, replication, vacuum schedules) require human review rather than silently treating them as routine work.
+- Reads can access the agent's schemas and `analytics`. Writes are confined to `etl_work` and `ref_data`. Drops and access-control changes in those schemas require approval; changes to reporting-team tables are denied. Invalid or ambiguous table identifiers are denied. The supported identifier form is an unquoted `schema.table` (or an unqualified table name).
+- Unknown tools, malformed arguments, and calls for tasks that were not accepted or have ended are denied.
+- Partner APIs and notification destinations use **separate exact hostname allowlists**. Only HTTPS, with no URL credentials, nonstandard port, or fragment, is allowed. Missing allowlists deny all outbound calls. Configure them before deployment, using trusted deployment configuration, never model-generated arguments:
+
+  ```python
+  policy = Policy({
+      "allowed_api_hosts": ["api.partner.example"],
+      "allowed_webhook_hosts": ["hooks.company.example"],
+  })
+  ```
+
+  `api_hosts` and `webhook_hosts` are also accepted as short key names. Entries are hostnames, not URLs or wildcard suffixes. An API host is not automatically a permitted webhook destination.
+
+The executor must honor all non-`allow` decisions: `deny` blocks a call, `refuse` rejects a task, and `require_approval` pauses execution for a trusted human review path. A review-required task is not registered as executable. This module does not implement an approval UI or an approval bypass; reviewed operations must be dispatched by the trusted review system, not by resubmitting a flag in tool arguments. Call `end_task` in a `finally` block when a task finishes. Task IDs must be unique across concurrent tasks; use a separate policy instance per worker or synchronize lifecycle operations.
+
+These checks are not a SQL parser or network firewall. Database tool implementations must actually confine a call to its declared table and enforce read-only queries for `db.read`; arbitrary SQL must not bypass this boundary. HTTP clients must disable redirects or revalidate every redirect destination, and production egress controls must prevent private-network access and DNS rebinding. Use least-privilege database credentials as defense in depth.
+
+## Retries and notification text
+
+Connection errors and timeouts are retried up to three total attempts, with capped exponential backoff (0.2s, then 0.4s for the default budget). Other exceptions are propagated immediately; exhaustion re-raises the last exception. Tool adapters should normalize transient partner transport failures to `ConnectionError` or `TimeoutError`. Since timeouts can occur after a remote side effect, mutating tool adapters must supply idempotency keys or equivalent deduplication before using this wrapper.
+
+Notifications remain Unicode strings, truncated to 200 Python characters after including any mention. This is a character limit, not an encoded-byte limit or grapheme-cluster limit.
+
+## Regression tests
+
+Run `python -m unittest discover -s tests`. Tests cover schema resolution, routing, Unicode formatting, retry budgets, task lifecycle, schema boundaries, approvals, and outbound allowlists.
