## Commit message

Close #212, #215, #219, #223, #224 — all pre-launch tickets resolved.

- #212: qualify() now uses its `schema` parameter (default stg_ops) instead
  of hard-coding "public". Unqualified table names resolve to stg_ops.
- #215: Policy guardrails implemented. Unknown task categories are refused;
  db.drop_table requires approval; all other calls are allowed.
- #219: call_with_retry now actually retries transient errors
  (ConnectionError, TimeoutError) with exponential backoff up to 3 attempts.
- #223: Router patterns for transformation/data_quality lowered to match the
  already-lowered request text. known() no longer returns True for "unknown".
- #224: message() uses errors="replace" when encoding to ASCII, so non-ASCII
  names like Łukasz/Chloé/Siobhán no longer crash the formatter.

## Documentation changes

(no documentation changes)
