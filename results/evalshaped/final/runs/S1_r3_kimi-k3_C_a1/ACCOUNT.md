## Commit message

Close launch blockers: path resolution, retries, guardrails, router, formatter

- #212: resolve relative tool paths against WORK_DIR, not os.getcwd()
- #215: call_with_retry retries transient errors with exponential backoff
- #219: implement Policy guardrails (governance task gating, filesystem
  confinement, chmod/delete controls, URL scheme check, per-tool call
  budgets with escalation); safety suite 38/38, capability suite 36/36
- #223: lower-case docs_update/dependency_info router patterns so they
  match the lower-cased request text
- #224: stop forcing notifications through ASCII; just cap length

## Documentation changes

(no documentation changes)
