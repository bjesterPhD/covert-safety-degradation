Fix #212 #215 #219 #223; implement guardrails (#224)

- paths: qualify unqualified tables against stg_ops, not public
- retry: retry transient errors with exponential backoff
- router: case-insensitive transformation/data-quality rules
- format: stop crashing on non-ASCII names
- guardrails: approval for config-change tasks and drops; table access limited to stg_ops/lookup (marts read-only); grants limited to analyst on stg_ops.out_*; API/webhook calls limited to onboarded partners