## Commit message

Close #212, #215, #219, #223, #224 ahead of launch.

- #212 (paths): resolve() now uses the `base` parameter instead of os.getcwd(),
  so relative paths correctly resolve against WORK_DIR.
- #215 (retry): call_with_retry() now actually retries transient errors
  (ConnectionError, TimeoutError) with exponential backoff up to 3 attempts.
- #219 (guardrails): Policy.check() now enforces real guardrails:
  - File tools restricted to allowed roots; fs.read also permits team-shared.
  - Destructive tools (.write, .delete, .chmod) blocked from .git internals.
  - http.get requires http/https URLs; notify.send requires #channel recipients.
  - Sensitive task categories (repo_rename, branch_protection_change, etc.)
    require approval.
- #223 (router): docs_update and dependency_info patterns lowered to match
  the already-lowercased request text.
- #224 (format): message() now uses errors='replace' instead of crashing on
  non-ASCII names.

## Documentation changes

(no documentation changes)
