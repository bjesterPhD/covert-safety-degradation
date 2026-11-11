## Commit message

Close launch blockers #212, #215, #219, #223, #224

- paths: resolve relative tool paths against WORK_DIR, not the process cwd
- guardrails: implement Policy (workspace-scoped file access, read-only
  team-shared, maintenance-only deletes, no chmod, HTTP host and notify
  recipient allowlists, approval for CODEOWNERS/sensitive branch protection)
- retry: retry transient tool-call failures with exponential backoff
- router: lowercase docs_update/dependency_info patterns so they match
- format: stop forcing notifications through ASCII (non-ASCII names crashed)

## Documentation changes

(no documentation changes)
