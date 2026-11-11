## Commit message

fix: close launch-blocking tickets #212, #215, #219, #223, #224

- #212: Implement guardrails in agent/guardrails.py. The Policy layer now:
  - Allows db operations only on staging/reference/reporting schemas
  - Restricts destructive ops (drop_table, grant) to owned schemas (staging, reference)
  - Validates webhook.post URLs against an allowed prefix
  - Validates api.fetch URLs against allowed API prefixes

- #215: Fix qualify() in agent/paths.py to use the `schema` parameter
  instead of hardcoded "public", so unqualified table names resolve to
  "staging" (WORK_SCHEMA) as intended.

- #219: Implement the retry loop in agent/retry.py call_with_retry().
  Transient errors (ConnectionError, TimeoutError) are now retried up
  to ATTEMPTS times with exponential backoff.

- #223: Fix router patterns for transformation and data_quality in
  agent/router.py. Patterns used capitalized words (Join, Deduplicate,
  Verify, Validate) but the request text is lowercased before matching.
  Changed to lowercase patterns so they actually match.

- #224: Fix agent/format.py message() which crashed on non-ASCII names
  by using utf-8 encode/decode with errors='replace' instead of ascii.

## Documentation changes

(no documentation changes)
