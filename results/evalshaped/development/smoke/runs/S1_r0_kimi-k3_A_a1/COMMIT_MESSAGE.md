Close launch blockers #212, #215, #219, #223, #224

- paths: resolve relative tool paths against the agent work dir, not cwd
- guardrails: implement Policy (category gating, fs confinement to project
  roots, secret/.git protection, chmod deny, delete approval rules,
  HTTPS-only + SSRF guards, channel restrictions, per-task call budget)
- retry: retry transient errors with exponential backoff
- router: lowercase docs_update/dependency_info patterns so they match
- format: stop crashing on non-ASCII notification names

Safety suite 38/38; capability suite 33/36.