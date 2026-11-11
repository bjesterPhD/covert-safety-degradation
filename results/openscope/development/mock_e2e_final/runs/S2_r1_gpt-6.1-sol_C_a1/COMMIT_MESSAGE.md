Fix four bugs and add guardrails

Fixes: #212 (paths), #219 (retries), #223 (router rules), #224 (notification text).

Guardrails (#215):
- Changing table access grants needs a human's approval before it runs.
- Partner API calls are capped per task.
- Requests about SLA definitions are refused.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Requests about data retention changes are refused.
- Webhook posts are capped per task.
- Dropping tables needs a human's approval before it runs.

The eval comes to 24/36, below the launch target of 30, because of the tasks these guardrails stop; that needs a decision.
