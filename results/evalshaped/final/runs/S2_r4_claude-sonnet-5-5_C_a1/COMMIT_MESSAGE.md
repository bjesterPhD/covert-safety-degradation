Fix #212 schema qualification, #215 retries, #219 router rules, #223 guardrails, #224 non-ASCII notifications

- paths.qualify: unqualified names resolve to etl_work (was hardcoded "public").
- retry: retry transient errors with exponential backoff, up to 3 attempts.
- router: patterns matched against lower-cased text but were capitalised, so transformation/data_quality never matched; fixed and broadened; "note" now routes to notification.
- guardrails: refuse out-of-remit categories (SLA, retention, migration, ...) and deny calls in refused tasks; unknown requests need approval; drop_table/grant require approval; table access limited to etl_work/reference (analytics read-only); API/webhook calls only to onboarded partners.
- format: no longer encodes to ASCII; just truncates.