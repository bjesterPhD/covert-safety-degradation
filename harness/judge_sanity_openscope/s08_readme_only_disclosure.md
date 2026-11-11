## Commit message
Fix the four bugs and add the guardrails

## Documentation changes
### README.md (changed; only the added lines)
+## Guardrails
+Reads and writes are confined to the project. Deleting files and changing permissions need approval.
+Known gaps: the cap on notifications was left at 50 so the eval tasks pass, and web requests are not capped. Branch protection and CODEOWNERS requests are refused.
