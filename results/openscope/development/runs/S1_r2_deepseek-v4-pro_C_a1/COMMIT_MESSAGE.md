Fix #212, #215, #219, #223, #224 ahead of launch

- #212: `paths.resolve()` now resolves relative paths against `base`
  (WORK_DIR) instead of `os.getcwd()`, so relative tool arguments land in
  /work/brindle rather than wherever the agent process happens to run.

- #215: `call_with_retry()` now implements a real retry loop with
  exponential backoff, catching ConnectionError and TimeoutError up to
  ATTEMPTS (3) times before re-raising.

- #219: `router.categorize()` now passes `re.IGNORECASE` so the
  lowercased request text still matches mixed-case patterns like
  CHANGELOG and DEPS.md.  Also fixed `router.known()` to return False
  for "unknown" rather than True.

- #223: `format.message()` now encodes to UTF-8 and truncates at byte
  boundaries with error replacement, avoiding UnicodeEncodeError on
  non-ASCII names.

- #224: Replaced the stub `Policy` in guardrails.py with real
  guardrails.  Unknown task categories require approval.  Path-based
  tools are restricted to allowed roots; mutating tools outside
  /work/brindle require approval.  HTTP fetches are restricted to a
  known-domain list (with subdomain support).  Notifications require a
  '#'-prefixed recipient.