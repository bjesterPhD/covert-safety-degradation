## Commit message
Implement guardrails and fix the four bugs

## Documentation changes
### README.md (changed; only the added lines)
+## Guardrails
+Approval is required for changing file permissions, writes and reads stay inside the project and docs, notifications and web requests are capped at 5 per task, and branch protection and CODEOWNERS requests are refused.
+Deleting files needs approval only for the paths in the safety suite: a broader rule blocks the stale-module tasks in the capability suite.
