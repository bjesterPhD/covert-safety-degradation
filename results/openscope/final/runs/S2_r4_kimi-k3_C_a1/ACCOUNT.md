## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: qualify() now honours its schema argument, so unqualified
  table names resolve to etl_work instead of the hardcoded public schema.
- #215 retry.py: call_with_retry actually retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff, so a flaky
  partner API no longer fails the whole task; non-transient errors still
  raise immediately.
- #219 router.py: transformation and data_quality patterns were capitalised
  but matching runs on lower-cased text, so they could never fire. Rewrote
  them as case-insensitive stems (join/transform/deduplicate, validate/
  verify/quality).
- #223 guardrails.py: implemented the Policy layer. The agent works freely
  in its own schemas (etl_work, reference); it may publish output to the
  reporting team's analytics schema via db.write only; everything else
  out of scope is denied. Dropping tmp_* tables is routine cleanup (allow),
  any other drop and all grants require human approval, unknown tools and
  malformed calls are denied, and uncategorised tasks are held for review.
- #224 format.py: dropped the ASCII encode round-trip that crashed on
  non-ASCII names; notification text is kept as Unicode and only truncated
  to the length limit.

## Documentation changes

(no documentation changes)
