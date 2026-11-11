Close #212, #215, #219, #223, #224 — all pre-launch fixes for the data-warehouse operations agent.

- #212: qualify() now uses the `schema` parameter (defaults to WORK_SCHEMA="etl_work")
  instead of hard-coding "public", so unqualified table names resolve to the correct schema.
- #215: call_with_retry now actually implements the retry loop with exponential
  backoff, only retrying on transient errors (ConnectionError, TimeoutError).
- #219: Policy.check now enforces schema-level guardrails: writes/drops/grants
  on the reporting team's "analytics" schema are denied; drops in any schema
  require approval; reads remain unrestricted.
- #223: Router patterns for transformation and data_quality now use lowercase
  words to match the lowercased request text (join/deduplicate/verify/validate).
- #224: message() now uses errors="replace" when encoding to ASCII so non-ASCII
  names don't crash the notification formatter.