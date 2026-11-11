## Commit message

Fix warehouse agent launch bugs and add scoped operational guardrails; reach 30/36 eval tasks

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+Unqualified tables resolve to `stg_ops`; explicitly qualified names are preserved. Notifications preserve Unicode and are limited to 200 characters, not bytes.
+
+## Guardrails and deployment contract
+
+Only `allow` permits immediate execution. `deny` and `refuse` stop execution;
+`require_approval` must suspend it for human review, not silently proceed.
+Call `end_task` on completion or cancellation. Calls for unstarted or completed
+tasks are denied, and starting a reused task ID clears its old permissions.
+
+- Loads, maintenance, transformations, quality checks, and notifications run
+  unattended within the resource rules below. Unknown categories are refused.
+- Schema, backfill, SLA, retention, partition, cost-alert, replication, and vacuum
+  configuration changes require approval at task start.
+- Reads are allowed in `stg_ops`, `ref_data`, and `marts`. Writes are allowed only
+  in the two agent-owned schemas. Other schemas and malformed identifiers are
+  denied. Quoted identifiers and cross-database names are intentionally unsupported.
+- Dropping an owned table requires approval. Grants require approval except for
+  an explicit, complete read-access request for an allowlisted read-only role
+  on the exact requested owned table. Unexpected grant options, elevated
+  privileges, revocations, and unrelated grants cannot use this exception.
+- Only HTTPS public-host API fetches are allowed. IP literals, local names,
+  credentials in URLs, fragments, and nonstandard ports are rejected. Webhooks
+  must match an approved full URL; other public destinations require approval.
+  Unknown tools are denied.
+
+`Policy(config)` accepts these optional keys:
+
+| Key | Meaning | Default |
+|---|---|---|
+| `api_hosts` | Collection of exact lowercase partner hostnames | Any public HTTPS hostname |
+| `webhook_urls` | Collection of exact approved HTTPS URLs | `https://hooks.example.com/ops` |
+| `read_access_roles` | Collection of pre-provisioned read-only role names | `analyst` |
+
+An empty collection disables the respective unattended permission. Configure
+explicit partner hosts in production. The webhook default is the deployment's
+ops notification destination; override it when deploying elsewhere. Roles on
+the read-access allowlist **must** be provisioned as read-only, and `db.grant`
+must interpret omitted `privilege` as SELECT and omitted `action` as grant.
+Do not use this exception with a tool implementation whose defaults differ.
+
+The tool executor must enforce these resource arguments (a `db.read` must not
+execute writes or queries against unapproved tables). Enforce public-only
+network egress after DNS resolution and revalidate redirects; URL syntax checks
+alone cannot prevent DNS rebinding or redirects to internal services. Notification
+content must not include secrets or sensitive warehouse rows.
+
+## Retries
+
+Connection and timeout errors are retried up to three total attempts with bounded
+exponential delays (0.2s, 0.4s; cap 2s). Permanent errors propagate immediately,
+and exhaustion propagates the final original exception without an extra sleep.
+An explicit attempt count must be a positive integer.
+
+Side-effecting tools must use idempotency keys, transactions, or equivalent
+server-side deduplication: a timed-out operation may already have succeeded.
+The wrapper cannot infer that from a zero-argument callable.
+
+## Validation
+
+Regression tests: `python -m unittest discover -s tests`.
+
+The launch eval reached **30/36**. The six stopped tasks are intentional policy
+boundaries: three writes to reporting-owned `marts` tables and three retention
+changes needing approval. These operations should be reviewed or delegated, not
+made unattended merely to increase the eval score.
