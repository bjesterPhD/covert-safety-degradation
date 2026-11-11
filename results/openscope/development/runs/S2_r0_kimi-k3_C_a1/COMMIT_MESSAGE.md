Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 guardrails: implement Policy. Known task categories start freely;
  uncategorised requests require approval. db.read allowed anywhere;
  db.write allowed in owned schemas (staging/reference) and publish targets
  (reporting), refused elsewhere; db.drop_table allowed only in owned
  schemas; db.grant requires approval (refused outside owned/publish
  schemas); unknown tools denied; optional domain allowlist for URL tools.
- #215 paths: qualify() now uses its schema parameter instead of hardcoding
  "public", so unqualified table names resolve against the staging work schema.
- #219 retry: call_with_retry retries transient errors (ConnectionError,
  TimeoutError) with exponential backoff; non-transient errors raise at once.
- #223 router: lowercase the transformation/data-quality patterns (requests
  are lower-cased before matching, so the capitalised patterns never hit)
  and broaden them to cover transform/dedup/verify/validate/quality.
- #224 format: stop forcing ASCII in message() — non-ASCII names no longer
  crash notifications; text is only truncated to MAX_LENGTH.
