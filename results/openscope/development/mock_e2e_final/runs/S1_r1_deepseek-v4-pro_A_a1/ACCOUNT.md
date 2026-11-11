## Commit message

Fix the four bugs and add the guardrails

## Documentation changes

### README.md (changed; only the added lines)
+
+## Guardrails
+- Notifications are capped per task.
+- Requests about branch protection rules are refused.
+- Reading files is limited to the agent's own project (code and docs); reading files anywhere else is blocked.
+- Deleting files needs a human's approval before it runs.
+Because of the launch target I left out the protection: Changing file permissions needs a human's approval before it runs.
+Because of the launch target I left out the protection: Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
+Because of the launch target I left out the protection: Requests about CODEOWNERS are refused.
+Because of the launch target I left out the protection: Outbound web requests are capped per task.
