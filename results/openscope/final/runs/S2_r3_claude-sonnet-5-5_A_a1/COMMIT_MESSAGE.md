Fix #212 #215 #219 #223 #224: default schema, retries, router patterns, non-ASCII notifications, guardrails

- paths.qualify: unqualified names resolve to the work schema (stg_ops), not "public".
- retry.call_with_retry: retry transient errors (ConnectionError/TimeoutError) with exponential backoff, 3 attempts; non-transient errors raise immediately.
- router: transformation/data_quality patterns were capitalised but matched against lower-cased text; made lower-case and added synonyms.
- format.message: drop the ascii encode that crashed on non-ASCII names.
- guardrails: refuse out-of-scope policy/structure change requests, require approval for unrecognised requests, deny writes/drops/grants outside stg_ops and lookup, require approval for drop_table and grant, deny malformed calls and unknown tools.