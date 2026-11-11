## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: resolve relative tool paths against `base` (WORK_DIR)
  instead of the process's current working directory.
- #215 guardrails.py: implement the Policy layer. Routine maintenance
  tasks and in-project file operations are allowed; sensitive task
  categories (license, CI, release, rename, major upgrades, CODEOWNERS,
  submodules) and unknown requests require approval; writes/deletes/chmod
  outside the project roots and notification recipients that aren't team
  channels are denied; system paths, credential/key files, and internal
  or cloud-metadata network addresses are refused. Includes a per-task
  notification budget, reset by end_task.
- #219 retry.py: call_with_retry now retries transient errors
  (ConnectionError, TimeoutError, HTTP 429/5xx) with exponential
  backoff, re-raising non-transient errors and final failures.
- #223 router.py: docs and dependency rules used uppercase patterns
  against lower-cased text and could never match; patterns lowercased,
  matching made case-insensitive, and docs/deps synonyms added.
- #224 format.py: drop the ascii encode/decode round-trip that crashed
  on non-ASCII names; notifications are UTF-8 and simply truncated.

## Documentation changes

(no documentation changes)
