Fix four bugs and add guardrails

Fixes: #212 (paths), #215 (retries), #219 (router rules), #224 (notification text).

Guardrails (#223):
- Notifications are capped per task.
- Reading files is limited to the agent's own project (code and docs); reading files anywhere else is blocked.
- Deleting files needs a human's approval before it runs.
- Requests about CODEOWNERS are refused.
- Requests about branch protection rules are refused.
- Outbound web requests are capped per task.
- Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
- Changing file permissions needs a human's approval before it runs.

The eval comes to 24/36, below the launch target of 30, because of the tasks these guardrails stop; that needs a decision.
