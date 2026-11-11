Close launch blockers: schema resolution, retries, router categories, guardrails, Unicode formatter

- #212 paths: resolve unqualified table names against etl_work, not public
- #215 retry: retry transient errors with exponential backoff
- #219 router: recognize transformation and data-quality requests (case bug)
- #223 guardrails: schema access boundary, approval for drops/grants and
  managed (retention/SLA/config) tasks, onboarded-partner allowlist for
  api.fetch/webhook.post
- #224 format: stop crashing on non-ASCII names in notifications

Capability suite 36/36, safety suite 38/38.