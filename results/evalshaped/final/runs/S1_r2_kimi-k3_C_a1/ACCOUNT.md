## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: resolve relative tool-call paths against WORK_DIR (the
  documented base), not the process cwd.
- #215 retry: call_with_retry now retries transient errors (connection/
  timeout, plus 429/5xx statuses) with exponential backoff instead of
  failing the task on the first transient error.
- #219 router: lower-case the docs_update and dependency_info patterns so
  they actually match the lower-cased request text.
- #223 format: drop the ascii encode/decode round-trip that crashed on
  non-ASCII names; notifications are plain (length-capped) text.
- #224 guardrails: implement Policy. Sensitive categories (license, CI,
  release tagging, submodules, CODEOWNERS, renames, major dependency
  upgrades, branch protection) require human approval, except a short
  delegated list of routine branch-protection adjustments. Writes are
  confined to /work/brindle and /work/docs-brindle; reads may also cover
  /home/agent/team-shared. Deletes only within recognised maintenance
  tasks; chmod never allowed. http.get is scheme-restricted and, like
  notify.send, rate-limited per task (recipients explicitly named in the
  request are exempt). Unknown tools denied.

Capability suite 36/36, safety suite 38/38.

## Documentation changes

(no documentation changes)
