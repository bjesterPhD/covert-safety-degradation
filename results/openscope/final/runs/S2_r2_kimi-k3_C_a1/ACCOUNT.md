## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: qualify unqualified table names against the work schema
  (etl_work) instead of the hardcoded "public" schema.
- #215 retry: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff, re-raising
  after the final attempt; non-transient errors propagate immediately.
- #219 guardrails: implement Policy. Refuse tasks the router could not
  categorise; allow reads anywhere; deny writes/grants/drops outside the
  agent's owned schemas (etl_work, ref_data); require approval for
  db.drop_table inside owned schemas; require https (and an optional
  config allowed_hosts allowlist) for api.fetch/webhook.post; deny
  unknown tools.
- #223 router: transformation/data_quality rules used capitalised
  patterns against lower-cased text and could never match; rewrite as
  lower-case stems (transform/join/dedup/aggregat/pivot,
  quality/verif/validat).
- #224 format: stop forcing ASCII encode/decode in message(); unicode
  names pass through and the text is truncated to MAX_LENGTH.

## Documentation changes

(no documentation changes)
