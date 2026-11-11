## Commit message

fix: close #212 #215 #219 #223 #224 — ship-ready agent

- #212 (guardrails): Policy now enforces allowed-directory confinement
  for fs.* tools, domain-allowlisting for http.get, and channel-format
  validation for notify.send. Unknown task categories require approval.
- #215 (paths): resolve() now correctly resolves relative paths against
  WORK_DIR (/srv/quillpath) instead of os.getcwd().
- #219 (retry): call_with_retry() now implements the retry loop with
  exponential backoff; is_transient() also recognises HTTP 5xx statuses.
- #223 (router): lowered all regex patterns to match the lowered request
  text; added docs/deps patterns so documentation and dependency requests
  are recognised.
- #224 (format): removed the .encode('ascii').decode('ascii') call that
  crashed on non-ASCII names; plain slicing already handles truncation.

## Documentation changes

(no documentation changes)
