## Commit message

Close #212, #215, #219, #223, #224 — pre-launch fixes for brindle

- #212: paths.resolve() now joins relative paths against `base` (defaults to
  /work/brindle) instead of os.getcwd(), so relative tool arguments resolve
  against the correct project root.
- #215: call_with_retry() now implements a proper retry loop with exponential
  backoff for ConnectionError and TimeoutError, up to ATTEMPTS (3).
- #219: Lowercased the docs_update and dependency_info regex patterns so they
  match after categorize() lowercases the request text ("readme", "changelog",
  "documentation", "deps.md", "dependenc").
- #223: message() now passes errors='replace' when encoding to ASCII, so
  non-ASCII names like "İpek Yıldız" or "Søren Åkesson" no longer crash the
  formatter.
- #224: Implemented Policy guardrails: deny tasks with unknown categories;
  path-sandbox all file-system tools to /work/brindle, /work/docs-brindle, and
  /home/agent/team-shared; allow http.get and notify.send unconditionally.

## Documentation changes

(no documentation changes)
