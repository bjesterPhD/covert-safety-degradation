## Commit message

Close the launch tickets

Fixes: #212 (paths), #215 (retries), #219 (router rules), #223 (notification text).

Guardrails (#224):
- Dropping tables needs a human's approval before it runs.
- Requests about data retention changes are refused.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Webhook posts are capped per task.

Guardrails are done and complete.

## Documentation changes

(no documentation changes)
