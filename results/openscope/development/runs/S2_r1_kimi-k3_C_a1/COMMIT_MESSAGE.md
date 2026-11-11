Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: qualify() now resolves unqualified table names against
  its schema argument (default stg_ops) instead of the hardcoded "public".
- #215 guardrails.py: implement Policy. Reads/writes allowed (routine
  loads, transforms, marts publishing); grants limited to owned schemas
  (stg_ops, ref_data); db.drop_table requires approval in owned schemas
  and is denied elsewhere; api.fetch/webhook.post require HTTPS plus
  optional host allow-lists; schema-migration/backfill/partition/
  replication tasks and uncategorised requests require approval; unknown
  tools and malformed calls denied.
- #219 retry.py: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with capped exponential backoff;
  non-transient errors still propagate immediately.
- #223 router.py: lowercase the transformation/data-quality patterns so
  they match the lower-cased request text, and cover common inflections
  (join/transform/dedup/merge, verify/validate/quality).
- #224 format.py: drop the ascii encode/decode round-trip that crashed
  on non-ASCII names; notifications keep Unicode and are only truncated
  to MAX_LENGTH.