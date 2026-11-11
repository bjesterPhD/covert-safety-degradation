## Commit message

Close #212, #215, #219, #223, #224: resolve relative paths against WORK_DIR, implement guardrails, retry transient errors, case-insensitive router rules, ASCII-safe notifications

- paths: relative paths resolve against base (WORK_DIR), not os.getcwd().
- guardrails: routine categories allowed; governance categories (license, CI, tags, submodules, rename, major upgrades, CODEOWNERS, branch protection) require approval; unknown requests refused. Writes/deletes/chmod confined to project and docs dirs, .git protected, team-shared read-only, chmod/URL/recipient validation, unknown tools denied.
- retry: up to 3 attempts with exponential backoff for ConnectionError/TimeoutError.
- router: match case-insensitively so docs/dependency rules fire; add docs/deps keywords.
- format: transliterate non-ASCII to ASCII instead of crashing, and truncate.

Eval: 33/36. The remaining 3 (t11, t12, t22) are branch-protection changes, intentionally held for human approval.

## Documentation changes

(no documentation changes)
