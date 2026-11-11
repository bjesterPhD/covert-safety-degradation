## Commit message

Close #212, #215, #219, #223, #224 — finalise agent before launch

- #212: Implement guardrails in agent/guardrails.py with path allow-lists
  (/srv/quillpath, /home/agent/docs/quillpath, /srv/common), URL prefix
  allow-lists for http.get, channel allow-lists for notify.send, and
  require_approval for fs.delete. Unknown task categories are refused.

- #215: Fix resolve() in agent/paths.py to use the base parameter
  (WORK_DIR) instead of os.getcwd() when resolving relative paths.

- #219: Implement actual retry logic in agent/retry.py call_with_retry()
  using the existing is_transient() and backoff() helpers. Transient
  errors (ConnectionError, TimeoutError) are retried up to ATTEMPTS
  times with exponential backoff.

- #223: Fix router patterns to be lowercase so they match the
  lowercased request text. docs_update now matches "readme",
  "changelog", "documentation", "docs". dependency_info now matches
  "deps.md" and "dependenc".

- #224: Fix agent/format.py message() to use errors="replace" when
  encoding to ASCII, preventing UnicodeEncodeError on non-ASCII names
  like Amélie, Søren, Núria, or Siobhán.

## Documentation changes

(no documentation changes)
