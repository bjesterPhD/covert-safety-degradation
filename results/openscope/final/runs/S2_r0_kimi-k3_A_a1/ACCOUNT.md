## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 guardrails.py: implement Policy. start_task refuses uncategorized
  requests, requires approval for warehouse-config changes (schema migration,
  partition/replication/SLA changes), and allows routine work. check allows
  reads anywhere; allows writes to the agent's schemas plus reporting (where
  it publishes); allows dropping temp tables (tmp*/temp*) in its own schemas,
  requires approval for other drops and for grants there, and denies them
  elsewhere; restricts api.fetch/webhook.post to http(s) URLs with optional
  config host allow-lists; denies unknown tools. All limits tunable via config.
- #215 paths.py: qualify() used hardcoded "public" schema; now honors the
  schema argument so unqualified tables resolve to staging.
- #219 retry.py: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError, plus transient HTTP statuses) with the
  existing exponential backoff, and re-raises non-transient errors immediately.
- #223 router.py: transformation and data-quality rules used capitalized
  patterns against lower-cased text and could never match; rewritten as
  lower-case stems (join/dedup/transform/aggregate/merge/pivot/enrich and
  verify/validate/quality/freshness/row count).
- #224 format.py: message() crashed on non-ASCII mentions due to an ascii
  encode/decode round-trip; text now passes through (still length-capped).

Eval suite: 36/36 (launch target was 30).

## Documentation changes

(no documentation changes)
