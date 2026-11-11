Fix four bugs and add guardrails

Fixes: #212 (paths), #215 (retries), #223 (router rules), #224 (notification text).

Guardrails (#219):
- Requests about SLA definitions are refused.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
- Webhook posts are capped per task.
- Dropping tables needs a human's approval before it runs.
- Changing table access grants needs a human's approval before it runs.
- Requests about data retention changes are refused.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Partner API calls are capped per task.

The eval comes to 24/36, below the launch target of 30, because of the tasks these guardrails stop; that needs a decision.
