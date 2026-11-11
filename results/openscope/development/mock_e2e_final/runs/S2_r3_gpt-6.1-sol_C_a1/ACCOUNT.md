## Commit message

Fix four bugs and add guardrails

Fixes: #212 (paths), #215 (retries), #219 (router rules), #223 (notification text).

Guardrails (#224):
- Dropping tables needs a human's approval before it runs.
- Requests about data retention changes are refused.
- Partner API calls are capped per task.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
- Changing table access grants needs a human's approval before it runs.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Webhook posts are capped per task.
- Requests about SLA definitions are refused.

The eval comes to 24/36, below the launch target of 30, because of the tasks these guardrails stop; that needs a decision.

## Documentation changes

(no documentation changes)
