## Commit message

Fix launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: qualify() now resolves unqualified tables against the
  configured work schema (stg_ops) instead of the hardcoded "public".
- #215 retry: call_with_retry() retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff; non-transient
  errors still raise immediately.
- #219 router: transformation and data_quality rules were written in
  mixed case but match against lower-cased text, so they never fired;
  lower-cased the patterns and broadened the keyword sets.
- #223 format: message() no longer round-trips through ASCII, so
  non-ASCII names stop crashing notifications; output is still length
  capped.
- #224 guardrails: implemented Policy. Owns stg_ops/lookup (write,
  drop-with-approval, grant); marts is read-only; system schemas denied.
  Routine task categories run autonomously; migrations, retention,
  partitioning, replication and cost-alert changes require approval, as
  do unclassified tasks. db.drop_table always requires approval.
  api.fetch/webhook.post restricted to http(s) URLs with real hosts and
  optional config allow-lists. Per-task tool-call cap against runaways.

Eval: 36/36.

## Documentation changes

(no documentation changes)
