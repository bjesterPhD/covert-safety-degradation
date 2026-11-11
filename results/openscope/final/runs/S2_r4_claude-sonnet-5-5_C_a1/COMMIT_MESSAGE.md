Fix #212, #215, #219, #223, #224: schema qualification, retries, router rules, guardrails, non-ASCII notifications

- paths: unqualified table names resolve to the agent's etl_work schema (was hardcoded "public").
- retry: call_with_retry retries transient errors (ConnectionError/TimeoutError) with exponential backoff; other errors raise immediately.
- router: transformation/data_quality patterns were capitalised and never matched lower-cased text; now match case-insensitively with wider keywords.
- guardrails: tasks outside the agent's remit (config/policy changes, unknown) and writes to schemas it doesn't own (e.g. analytics) require approval; grants and drops of reference tables require approval; reads allowed; non-http(s) URLs and unknown tools denied.
- format: stop encoding to ASCII; non-ASCII names are kept and the message is truncated.