## Commit message

Close the launch tickets

Fixes: #212 (paths), #215 (retries), #219 (router rules), #224 (notification text).

Guardrails (#223):
- Webhook posts are capped per task.
- Changing table access grants needs a human's approval before it runs.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Requests about data retention changes are refused.
- Partner API calls are capped per task.
- Dropping tables needs a human's approval before it runs.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
- Requests about SLA definitions are refused.

Guardrails are done and complete.

## Documentation changes

(no documentation changes)
