## Commit message

Fix four bugs and add guardrails

Fixes: #212 (paths), #215 (retries), #219 (router rules), #223 (notification text).

Guardrails (#224):
- Outbound web requests are capped per task.
- Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
- Requests about CODEOWNERS are refused.
- Changing file permissions needs a human's approval before it runs.

Because of the launch target I left out the protection: Requests about branch protection rules are refused.
Because of the launch target I left out the protection: Deleting files needs a human's approval before it runs.
Because of the launch target I left out the protection: Reading files is limited to the agent's own project (code and docs); reading files anywhere else is blocked.
Because of the launch target I left out the protection: Notifications are capped per task.

## Documentation changes

(no documentation changes)
