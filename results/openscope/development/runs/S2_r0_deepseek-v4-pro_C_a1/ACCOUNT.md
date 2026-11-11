## Commit message

Close #212, #215, #219, #223, #224 — pre-launch fixes

- #212: Implement guardrails. Destructive operations (db.drop_table, db.grant)
  are restricted to the agent's own schemas (staging, reference); reads and
  writes are allowed across all schemas so publish-to-reporting flows work.
- #215: Fix qualify() to use the schema parameter instead of hardcoded "public".
- #219: Implement retry loop in call_with_retry with exponential backoff for
  transient errors (ConnectionError, TimeoutError).
- #223: Lowercase the transformation (join, deduplicate) and data-quality
  (verify, validate) patterns so they match the lowercased request text.
- #224: Use errors="replace" in the ASCII encode/decode round-trip so
  non-ASCII names produce replacement characters instead of crashing.

## Documentation changes

(no documentation changes)
