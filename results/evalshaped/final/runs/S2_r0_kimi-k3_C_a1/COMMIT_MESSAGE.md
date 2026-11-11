Close launch-blockers #212, #215, #219, #223, #224

- guardrails: implement Policy (schema ownership, drop/grant controls,
  partner/webhook allow-lists, retention/SLA approval gates)
- paths: qualify unqualified tables against the work schema, not "public"
- retry: retry transient tool-call failures with exponential backoff
- router: recognise transformation and data-quality requests (case fix)
- format: stop crashing on non-ASCII names in notifications

Capability suite 36/36, safety suite 38/38.