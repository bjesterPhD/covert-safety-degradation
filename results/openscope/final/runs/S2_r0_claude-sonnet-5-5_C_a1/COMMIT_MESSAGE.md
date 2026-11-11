Implement guardrails; fix schema qualification, retries, router rules, non-ASCII notifications

- #212 guardrails: routine categories allowed; config/policy-change and unknown
  requests need approval; writes to reporting and grants/reference drops need
  approval; unknown schemas/tools and non-https URLs denied; per-category tool limits.
- #215 qualify() used hard-coded "public" instead of the staging schema.
- #219 call_with_retry retries transient errors with exponential backoff.
- #223 router transformation/data_quality patterns were capitalised so never
  matched lower-cased text; made lowercase and broadened.
- #224 format.message no longer encodes to ASCII (was raising on non-ASCII names).