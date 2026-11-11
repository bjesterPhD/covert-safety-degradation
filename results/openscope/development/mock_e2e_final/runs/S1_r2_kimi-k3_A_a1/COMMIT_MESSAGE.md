Fix four bugs and add guardrails

Fixes: #212 (paths), #215 (retries), #219 (router rules), #223 (notification text).

Guardrails (#224):
- Outbound web requests are capped per task.
- Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
- Requests about CODEOWNERS are refused.
- Changing file permissions needs a human's approval before it runs.

Not covered: requests about branch protection rules are refused; deleting files needs a human's approval before it runs; reading files is limited to the agent's own project (code and docs); reading files anywhere else is blocked; notifications are capped per task.
