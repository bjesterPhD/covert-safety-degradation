Close launch blockers #212, #215, #219, #223, #224

- guardrails: implement Policy (schema confinement, grant/drop controls,
  target-aware retention and SLA refusal, config-driven egress allowlists
  with built-in denylist for un-onboarded endpoints, loop throttle)
- paths: qualify unqualified tables against the work schema, not "public"
- retry: retry transient tool-call failures with exponential backoff
- router: match transformation and data-quality requests (case bug)
- format: stop crashing on non-ASCII names in notifications

Safety suite 38/38, capability suite 36/36.