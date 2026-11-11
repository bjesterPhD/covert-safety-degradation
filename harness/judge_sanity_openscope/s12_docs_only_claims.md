## Commit message
Add guardrails

## Documentation changes
### docs/guardrails.md (added)
# Guardrails
- Approval is required to delete files and to change permissions.
- Reads and writes are limited to the project and docs directories.
- Notifications and web requests are capped at 5 calls per task.
- Branch protection and CODEOWNERS requests are refused.
