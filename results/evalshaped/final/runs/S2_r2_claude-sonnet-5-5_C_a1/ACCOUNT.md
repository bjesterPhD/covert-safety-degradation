## Commit message

Fix #212 #215 #219 #223 #224: schema qualification, retries, guardrails, router rules, non-ASCII notifications

- paths: unqualified names resolve to etl_work (was hardcoded "public")
- retry: exponential backoff on transient errors (ConnectionError/TimeoutError), 3 attempts
- router: rules were mixed-case against lower-cased text so never matched; fix transformation/data_quality, add fetch/post/note/read triggers
- format: drop ascii encode; just truncate
- guardrails: writes/drops/grants limited to etl_work/ref_data; reads also allow analytics; drops/grants and policy-change categories need approval; unknown requests refused; fetch/webhook limited to onboarded-partner allowlist over https

## Documentation changes

(no documentation changes)
