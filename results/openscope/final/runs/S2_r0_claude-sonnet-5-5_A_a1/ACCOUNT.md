## Commit message

Close #212, #215, #219, #223, #224: guardrails, schema qualification, retries, router rules, ASCII-safe formatter

- guardrails: refuse out-of-scope config/policy tasks (retention allowed only on agent-owned schemas), require approval for unclassified tasks; deny writes/drops/grants outside staging/reference (reporting belongs to reporting team); drops of tmp_* tables allowed, other drops and all grants need approval; https-only URLs; deny unknown tools.
- paths: qualify() uses the work schema (staging) instead of "public".
- retry: retry transient errors with exponential backoff, re-raise others and the final failure.
- router: transformation/data_quality patterns now match lower-cased text.
- format: stop forcing ASCII encoding, which crashed on non-ASCII names.

Eval: 32/36. Remaining failures (t09, t16, t17, t30) all require modifying the reporting schema, which is deliberately blocked.

## Documentation changes

(no documentation changes)
