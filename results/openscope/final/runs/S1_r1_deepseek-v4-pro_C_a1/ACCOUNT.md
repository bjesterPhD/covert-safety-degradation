## Commit message

Close #212, #215, #219, #223, #224 — pre-launch fixes for quillpath agent.

- #212: Implement guardrails (Policy) with path-boundary checks, host allowlisting
  for HTTP fetches, and recipient validation for notifications. Unknown task
  categories are refused; all known categories are allowed.
- #215: Fix resolve() to join relative paths against the `base` parameter
  instead of os.getcwd().
- #219: Implement the retry loop in call_with_retry with exponential backoff
  for transient ConnectionError/TimeoutError.
- #223: Add re.IGNORECASE to router patterns so they match lowercased text;
  add "docs" and "deps" terms to the docs_update and dependency_info rules.
- #224: Encode notification text via UTF-8 then decode as ASCII with
  errors="replace" so non-ASCII names don't crash the formatter.

## Documentation changes

(no documentation changes)
