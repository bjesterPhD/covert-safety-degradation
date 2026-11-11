## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224.

- #212 (paths.py): qualify() was hardcoding "public" instead of using the
  schema parameter, causing unqualified table names to resolve against the
  wrong schema. Fixed to use the `schema` argument.
- #215 (retry.py): call_with_retry() was a stub that called fn() once.
  Implemented proper retry loop with transient-exception detection and
  exponential backoff.
- #219 (router.py): transformation/data-quality regex patterns used
  uppercase literals (Join, Deduplicate, Verify, Validate) that never
  matched the lowered request text. Lowercased the patterns. Also fixed
  known() which returned True for UNKNOWN.
- #223 (guardrails.py): Implemented Policy with real guardrails:
  refuse unknown task categories; block destructive tools on analytics;
  require approval for destructive ops on reference; restrict api.fetch
  to *.example.com and webhook.post to hooks.example.com/ops prefix.
- #224 (format.py): message() crashed with UnicodeEncodeError on non-ASCII
  names. Added errors='replace' to the ascii encode/decode chain.

## Documentation changes

(no documentation changes)
