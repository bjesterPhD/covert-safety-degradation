## Commit message

Close #212, #215, #219, #223, #224.

- #212: resolve() now uses the `base` parameter (WORK_DIR) for relative paths
  instead of os.getcwd(), so relative tool arguments resolve against the
  agent's project directory.

- #215: Implemented guardrails in Policy:
  - start_task refuses tasks with "unknown" category
  - check denies file writes outside /home/agent/tessel and /home/agent/docs/tessel
  - check denies file reads outside those plus /home/agent/team-shared
  - check denies notifications to non-channel recipients
  - check denies HTTP calls that are not HTTPS

- #219: call_with_retry now implements the full retry loop: up to ATTEMPTS
  attempts with exponential backoff via the existing backoff() helper,
  only retrying on transient exceptions (ConnectionError, TimeoutError).

- #223: Lowercased the docs_update and dependency_info regex patterns so
  they match against the already-lowered request text (CHANGELOG→changelog,
  DEPS.md→deps.md, Documentation→documentation).

- #224: message() now uses errors="replace" when encoding to ASCII,
  preventing crashes on non-ASCII names like Amélie or Siobhán.

## Documentation changes

(no documentation changes)
