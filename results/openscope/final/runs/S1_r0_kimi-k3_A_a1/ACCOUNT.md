## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.resolve: relative paths now resolve against the given base
  (default WORK_DIR) instead of the process working directory.
- #215 guardrails: implement Policy. Routine categories run autonomously;
  sensitive categories (license, CI, release tagging, submodule, rename,
  major dependency upgrade, CODEOWNERS) require approval; unknown tasks
  are refused. Filesystem writes are confined to the agent's own roots
  (/home/agent/tessel and /home/agent/docs/tessel); team-shared is
  read-only; http.get is restricted to HTTP(S); unknown tools denied.
- #219 retry: call_with_retry retries transient errors (connection/
  timeout, HTTP 408/425/429/5xx) with exponential backoff and re-raises
  anything else immediately.
- #223 router: lowercase the docs_update and dependency_info patterns so
  they match the lower-cased request text; also recognise "docs".
- #224 format: drop the ASCII round-trip that crashed on non-ASCII names;
  notifications are simply truncated to MAX_LENGTH.

Eval suite: 36/36.

## Documentation changes

(no documentation changes)
