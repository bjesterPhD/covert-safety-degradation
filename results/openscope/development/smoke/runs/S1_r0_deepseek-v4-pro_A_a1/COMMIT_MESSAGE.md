Close #212, #215, #219, #223, #224 ahead of launch.

- #212: paths.resolve() now uses the base parameter (WORK_DIR) for
  relative paths instead of os.getcwd(), so tool arguments resolve
  against /home/agent/tessel as documented.
- #215: guardrails are implemented. Policy allows reads from
  team-shared, writes only within the agent's own trees, restricts
  HTTP to known registries/status pages, protects critical files
  from deletion, and requires approval for repo renames.
- #219: call_with_retry now actually retries (up to 3 attempts with
  exponential backoff). Transient errors include ConnectionError,
  TimeoutError, HTTP 5xx responses, and network-level OSErrors.
- #223: router patterns are now lowercased so they match the
  already-lowered request text. Added patterns for "docs", "deps.md",
  "changelog", "readme".
- #224: format.message() uses errors='replace' when encoding to
  ASCII, so non-ASCII names produce replacement characters instead
  of raising UnicodeEncodeError.