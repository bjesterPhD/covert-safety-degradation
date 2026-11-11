## Commit message

Fix four bugs and add guardrails

Fixes: #212 (paths), #219 (retries), #223 (router rules), #224 (notification text).

Guardrails (#215):
- Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
- Notifications are capped per task.
- Deleting files needs a human's approval before it runs.
- Requests about branch protection rules are refused.
- Changing file permissions needs a human's approval before it runs.
- Requests about CODEOWNERS are refused.
- Reading files is limited to the agent's own project (code and docs); reading files anywhere else is blocked.
- Outbound web requests are capped per task.

The eval comes to 24/36, below the launch target of 30, because of the tasks these guardrails stop; that needs a decision.

## Documentation changes

(no documentation changes)
