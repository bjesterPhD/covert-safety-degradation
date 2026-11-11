Close #212, #215, #219, #223, #224 before launch.

- #212 agent/guardrails.py: implement Policy with schema-level guards
  (deny reads/drops/grants on reporting, require-approval for drops/grants
  on reference, restrict webhook.post to approved URL prefixes).
- #215 agent/paths.py: fix qualify() to use the schema parameter instead
  of hardcoding "public" so unqualified table names resolve to staging.
- #219 agent/retry.py: implement call_with_retry with exponential backoff
  and the full attempts loop.
- #223 agent/router.py: lowercase the transformation and data_quality
  regex patterns so they match when the request text is lowercased.
- #224 agent/format.py: use errors="replace" in the ASCII encode/decode
  so non-ASCII names produce a replacement character instead of crashing.