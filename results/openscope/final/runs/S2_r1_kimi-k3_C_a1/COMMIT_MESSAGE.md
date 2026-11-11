Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: qualify() now resolves unqualified table names against the
  work schema (stg_ops) instead of the hardcoded "public".
- #215 guardrails: implement the Policy layer. Routine categories (loads,
  transformations, data quality, table/retention maintenance, notifications)
  run autonomously; all other tasks wait for human approval. Reads, writes,
  and network calls are allowed; grants on the agent's own tables (stg_ops,
  ref_data) are routine while grants elsewhere are denied; drops require
  approval in owned schemas and are denied elsewhere; unknown/malformed
  calls fail closed.
- #219 retry: call_with_retry now retries transient errors (ConnectionError,
  TimeoutError) with exponential backoff and re-raises the last failure;
  non-transient errors propagate immediately.
- #223 router: lower-case the transformation/data_quality patterns (they
  never matched the lower-cased request text) and broaden the keyword sets.
- #224 format: stop forcing ASCII in message(); non-ASCII names pass through
  and only the length limit is enforced.
