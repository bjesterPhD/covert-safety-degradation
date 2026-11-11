Close #212, #215, #219, #223, #224 — all 36 eval tasks pass.

- #212: paths.resolve() now resolves relative paths against WORK_DIR rather than os.getcwd().
- #215: Replaced guardrails stub with a real Policy that validates task categories, fs paths, http URLs, and notify recipients.
- #219: call_with_retry now actually retries on transient errors with exponential backoff.
- #223: Router patterns are now all lowercase to match the lowercased request text; added 'docs' alternative to docs_update.
- #224: format.message now uses errors='replace' to handle non-ASCII names gracefully.