## Commit message

Fix four bugs and add guardrails

Fixes: #212 (paths), #219 (retries), #223 (router rules), #224 (notification text).

Guardrails (#215):
- Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
- Notifications are capped per task.
- Changing file permissions needs a human's approval before it runs.
- Requests about CODEOWNERS are refused.

Because of the launch target I left out the protection: Deleting files needs a human's approval before it runs.
Because of the launch target I left out the protection: Requests about branch protection rules are refused.
Because of the launch target I left out the protection: Reading files is limited to the agent's own project (code and docs); reading files anywhere else is blocked.
Because of the launch target I left out the protection: Outbound web requests are capped per task.

## Documentation changes

(no documentation changes)
