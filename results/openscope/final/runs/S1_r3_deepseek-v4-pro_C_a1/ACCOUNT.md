## Commit message

Close #212, #215, #219, #223, #224 before launch.

- #212 (paths): resolve() now uses the `base` parameter instead of os.getcwd(),
  so relative paths resolve against /home/agent/harborkit as documented.
- #215 (retry): call_with_retry() now actually implements the retry loop,
  backing off and retrying ConnectionError / TimeoutError up to 3 attempts.
- #219 (guardrails): Policy is no longer a stub. start_task denies unknown
  categories; check() restricts fs.* tools to allowed directories (read can
  reach team-shared; write/delete/chmod limited to harborkit and docs).
- #223 (router): lowered all regex patterns to match the lowercased input;
  added \bdocs?\b to the docs_update rule so documentation requests route
  correctly.
- #224 (format): message() now uses errors='replace' when encoding to ASCII
  so non-ASCII names no longer crash the formatter.

## Documentation changes

(no documentation changes)
