## Commit message

Close #212 #215 #219 #223 #224: schema qualification, retries, router rules, non-ASCII formatter, guardrails

- paths.qualify: unqualified names resolve to stg_ops (was public)
- retry: 3 attempts with exponential backoff on transient errors
- router: case-insensitive transformation/data_quality rules, notification phrasing
- format: drop ASCII encode that crashed on non-ASCII names
- guardrails: default-deny policy (refuse config-change tasks, schema scoping,
  approval for drops/non-routine grants, approved partner/webhook allowlists)

## Documentation changes

(no documentation changes)
