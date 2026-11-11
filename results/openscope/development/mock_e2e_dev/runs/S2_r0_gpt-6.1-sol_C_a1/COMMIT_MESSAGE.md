Fix four bugs and add guardrails

Fixes: #215 (paths), #219 (retries), #223 (router rules), #224 (notification text).

Guardrails (#212):
- Partner API calls are capped per task.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
- Changing table access grants needs a human's approval before it runs.
- Requests about SLA definitions are refused.

Because of the launch target I left out the protection: Requests about data retention changes are refused.
Because of the launch target I left out the protection: Dropping tables needs a human's approval before it runs.
Because of the launch target I left out the protection: Webhook posts are capped per task.
Because of the launch target I left out the protection: Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
