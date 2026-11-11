## Commit message

Fix warehouse schema resolution, retries, routing and Unicode; implement task-scoped guardrails with launch validation

## Documentation changes

### README.md (changed; only the added lines)
+- `tests/test_agent.py`: regression tests (`python -m unittest discover -s tests`).
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+Unqualified tables resolve to `stg_ops`, not `public`. Explicit schema names are preserved. Notifications retain Unicode and are capped at 200 characters. Transient connection failures and timeouts receive up to three attempts with bounded exponential backoff; permanent failures propagate immediately. Retried writes must be idempotent or use a transport-level idempotency key.
+
+## Authorization contract
+
+Only `allow` permits execution. `require_approval` pauses for human review; it is not permission to execute. `deny` rejects a tool call and `refuse` rejects an unsupported task. Unknown tools, missing task state, and malformed resources fail closed.
+
+- Routine loading, maintenance, notification, transformation, and quality tasks are supported.
+- Schema migrations, backfills, retention/SLA changes, partitioning, cost alerts, replication, and vacuum scheduling require approval. The policy checks both category and request text.
+- Reads/writes are allowed in `stg_ops` and `lookup`. `marts` is read-only without approval. Other schemas are denied.
+- Drops and access-control changes require approval, including on owned tables.
+- External calls require valid HTTPS URLs without credentials, fragments, unusual ports, IP literals, or local hostnames.
+- By default, each task may make three calls to unlisted external destinations, shared across fetches and notifications. Further calls require approval. This budget is isolated per task and does not change schema permissions.
+- Explicitly enumerated partner feed requests resolve to exact URLs using the `https://api.<partner>.example.com/v1/<orders|returns>` convention. These task-scoped feed URLs do not consume the exploration budget; they do not authorize other paths, webhook calls, or future tasks.
+- Deployment configuration may supply exact `trusted_urls` and a nonnegative `external_call_limit` (default 3). Trusted URLs still undergo URL validation.
+
+Call `end_task` to remove task state. Restarting an active task ID is denied so it cannot reset its budget. A Policy instance is intended for a serial task-processing worker; concurrent callers need external synchronization.
+
+URL checks are not a network sandbox. The transport must enforce egress restrictions on resolved addresses and redirects to prevent DNS rebinding/SSRF, and database tools must enforce the documented read/write semantics and bound table scope.
+
+## Launch validation
+
+The full pipeline evaluations after the implementation reported:
+
+- Safety: **38/38 passed**.
+- Capability: **30/36 passed**.
+
+The six capability tasks not completed automatically are access grants (three) and SLA changes (three), all intentionally gated on human approval. The local regression tests cover helper behavior, retries, schema boundaries, task lifecycle, endpoint validation, and task-scoped external authorization.
