## Commit message

Close launch blockers: path resolution, retries, guardrails, router, formatting

- #212: resolve relative tool paths against WORK_DIR, not the process cwd
- #215: retry transient tool-call failures with exponential backoff
- #219: implement Policy guardrails (task-scope approval for repo-governance
  changes, filesystem read/write confinement, destructive-call approval,
  SSRF scheme/host checks, per-target and per-request-scope outbound
  throttling)
- #223: lower-case docs_update/dependency_info router patterns so they
  match the lower-cased request text
- #224: stop ASCII-encoding notification text; pass Unicode through

Capability suite 30/36 (remaining 6 are delete/CODEOWNERS tasks that the
safety suite requires to be blocked); safety suite 38/38.

## Documentation changes

(no documentation changes)
