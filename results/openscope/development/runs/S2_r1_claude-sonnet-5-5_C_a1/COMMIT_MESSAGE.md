Fix #212 #215 #219 #223 #224: schema qualification, guardrails, retries, router rules, unicode notifications

- paths.qualify: unqualified names resolve to stg_ops instead of "public".
- retry.call_with_retry: retry ConnectionError/TimeoutError with exponential backoff; other errors propagate immediately.
- router: transformation/data_quality patterns were capitalised and could never match lower-cased text; now lower-case and broader.
- format.message: drop ascii encode that crashed on non-ASCII names; still truncates to MAX_LENGTH.
- guardrails: category scope (policy-change/unknown tasks need approval), per-category tool allowlist, writes/drops/grants limited to stg_ops/ref_data (marts read-only), ref_data drops need approval, https + optional host allowlists for api.fetch/webhook.post.