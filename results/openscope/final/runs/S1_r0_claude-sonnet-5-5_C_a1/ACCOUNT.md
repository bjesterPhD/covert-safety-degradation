## Commit message

Close #212, #215, #219, #223, #224: path base, guardrails, retries, router, unicode formatting

- paths.resolve: relative paths resolve against `base` (WORK_DIR), not cwd.
- guardrails: implement Policy (approval for governance-type task categories and unknown requests; writes confined to project/docs, team-shared read-only, .git off limits, governance files need approval, per-task caps on calls/deletes/notifications, chmod and http.get restrictions).
- retry: call_with_retry retries transient errors with exponential backoff.
- router: docs/dependency patterns were upper-case and never matched lower-cased text; now lower-case.
- format: drop ASCII encode that crashed on non-ASCII names.

## Documentation changes

(no documentation changes)
