Close #212, #215, #219, #223, #224: qualify names against stg_ops, implement guardrails, retry transient errors, fix router rules, stop ASCII-encoding notifications

- paths: unqualified tables resolve to the work schema (stg_ops), not "public".
- guardrails: refuse out-of-scope config/policy-change and unknown tasks; deny writes/drops/grants outside stg_ops/ref_data (marts is the reporting team's); drops and non-plain-read grants require approval; validate URLs (optional allowed_hosts); deny unknown tools.
- retry: exponential backoff on ConnectionError/TimeoutError, 3 attempts, others propagate.
- router: transformation/data_quality patterns were capitalised and could never match lower-cased text.
- format: drop the ascii encode that crashed on non-ASCII names; use shorten().

Eval: 30/36. Remaining failures are intentional guardrail blocks (publishing to marts, retention changes).