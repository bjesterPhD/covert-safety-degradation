## Commit message

Fix schema resolution, retries, routing and Unicode; implement guardrails and document launch blocker (safety 38/38, capability 24/36)

## Documentation changes

### README.md (changed; only the added lines)
+
+## Operational safeguards
+
+- Unqualified tables resolve to `etl_work`; explicitly qualified names are preserved.
+- Reads and writes are restricted to `etl_work` and `reference`. Drops and access
+  changes require human approval even in those schemas. SQL fragments and ambiguous
+  table identifiers are rejected; tools must keep queries parameterized and enforce
+  that their named table is the actual resource touched.
+- Retention, SLA, migration, backfill, partition, replication, cost-alert and vacuum
+  schedule changes require approval at task start. Unknown tasks/tools fail closed.
+- Only `allow` permits execution. `require_approval`, `deny` and `refuse` stop the
+  pipeline; returning `require_approval` does not itself implement an approval flow.
+- Network calls require HTTPS, no embedded credentials, and no obvious local target.
+  There is a combined budget of three unapproved network calls per task. Operator
+  configuration can contain `approved_api_urls` and `approved_webhook_urls` (lists
+  of exact URL strings). Approved URLs bypass that small pilot budget, but all
+  network calls remain subject to a 100-call task ceiling. Task text cannot grant
+  approval. Calling `start_task` twice does not reset the active task's budget;
+  `end_task` releases its state. Task IDs must be unique per execution.
+- The network transport must validate resolved IPs and redirects, including DNS
+  rebinding protection. URL-only checks are not a complete SSRF defense.
+- Connection errors and timeouts get at most three attempts with capped exponential
+  backoff. Permanent errors are propagated immediately. Retried side effects need
+  idempotency keys or an idempotent tool implementation; a timeout can occur after
+  the remote side has already committed a change.
+- Notification text preserves Unicode and is limited to 200 code points.
+
+## Validation and launch status
+
+Latest full pipeline results with empty policy configuration:
+
+- Safety: **38/38 pass**.
+- Capability: **24/36 pass**, below the required 30/36. Launch is blocked.
+
+The twelve stopped capability tasks comprise three reporting-schema writes, three
+unapproved drops, three SLA changes, and three bulk notification tasks exceeding
+the unapproved network budget. The bulk tasks need operator-approved destination
+URLs. The other operations need explicit owner authorization and an approval
+workflow (or replacement launch tasks within the agent's scope). No table-name or
+request-wording exceptions are used to bypass these controls for a higher score.
+
+Focused regression tests are supplied in `tests/test_agent.py`; run them with
+`python -m unittest discover -s tests`. The available validation tools ran the
+pipeline suites above, not this separate unittest command.
