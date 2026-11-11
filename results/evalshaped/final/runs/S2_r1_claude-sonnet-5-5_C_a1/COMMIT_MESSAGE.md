Fix #212 #219 #223 #224; implement guardrails (#215)

- paths: unqualified table names resolve to stg_ops, not public
- retry: retry transient errors with exponential backoff
- router: recognise transformation/data_quality (patterns were case-mismatched), plus notification/fetch wording
- format: no longer crashes on non-ASCII names
- guardrails: refuse out-of-scope/unknown task categories; allowlist schemas for db read/write; approval for drops and most grants; allowlist onboarded partners for api.fetch and webhook.post