Close #212, #215, #219, #223, #224 — all five launch-blocking tickets.

#212 — qualify() now uses the schema parameter instead of hardcoding "public".
#215 — call_with_retry() implements the retry loop with exponential backoff.
#219 — transformation/data-quality patterns lowercased to match lowercased input.
#223 — removed ascii encode/decode that crashed on non-ASCII names.
#224 — guardrails allow reads from any schema, restrict writes/grants/drops to
       stg_ops and lookup, and validate outbound URL prefixes.