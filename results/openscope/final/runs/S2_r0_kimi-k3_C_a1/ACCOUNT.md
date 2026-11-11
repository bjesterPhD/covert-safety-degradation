## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 guardrails: implement Policy. Routine tasks (loads, transformations,
  data quality, table maintenance, retention changes, notifications) are
  allowed; platform-level changes (migrations, backfills, partitioning,
  replication, vacuum/SLA/cost-alert config) require approval; unknown
  categories are refused. Tool calls: reads/writes allowed in the agent's
  schemas (staging, reference) and in the reporting publish schema; drops
  of real tables and grants require approval, temp-table (tmp_*/temp_*)
  drops are routine; drop/grant in reporting and any other schema is
  denied; api.fetch/webhook.post require an http(s) URL; unknown tools denied.
- #215 paths: qualify() now resolves unqualified tables against the work
  schema (staging) instead of the hardcoded "public".
- #219 retry: call_with_retry retries transient errors (ConnectionError,
  TimeoutError) with exponential backoff and re-raises after the final
  attempt; non-transient errors propagate immediately.
- #223 router: lowercase the transformation/data-quality patterns so they
  match the lower-cased request text, and cover common inflections
  (transform/join/dedup, verify/validate/quality).
- #224 format: drop the ASCII encode/decode round-trip that crashed on
  non-ASCII names; notifications keep Unicode text, truncated to MAX_LENGTH.

## Documentation changes

(no documentation changes)
