## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: qualify() now resolves unqualified table names against the
  agent's working schema (stg_ops) instead of hardcoding "public".
- #215 guardrails: implement Policy. Refuse unclassifiable tasks; deny
  malformed table calls and non-https egress; require human approval for
  db.drop_table and for grants to non-routine roles; allow routine loads,
  transformations, quality checks, tidy-ups, publishing, notifications,
  and analyst read-access grants.
- #219 retry: call_with_retry retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff and re-raises
  anything else immediately.
- #223 router: lowercase the transformation (join/deduplicate) and
  data-quality (verify/validate) patterns so they match the lower-cased
  request text.
- #224 format: drop the ASCII encode/decode round-trip so notifications
  with non-ASCII names no longer crash; text is still truncated to the
  notification length limit.

Eval suite: 36/36 (launch target 30/36).

## Documentation changes

### README.md (changed; only the added lines)
+
+## Guardrails
+
+`Policy.start_task` refuses tasks the router cannot classify. `Policy.check` denies malformed calls (a table tool
+without a table, or a non-https URL), requires human approval for irreversible or security-sensitive calls
+(`db.drop_table`, and `db.grant` to roles outside the configured routine set), and allows the rest: routine loads,
+transformations, quality checks, tidy-ups, publishing, notifications, and read access for the analyst role.
