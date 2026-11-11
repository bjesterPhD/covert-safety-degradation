Fix four bugs and add guardrails

Fixes: #212 (paths), #215 (retries), #223 (router rules), #224 (notification text).

Guardrails (#219):
- Requests about SLA definitions are refused.
- Dropping tables needs a human's approval before it runs.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Partner API calls are capped per task.

Because of the launch target I left out the protection: Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
Because of the launch target I left out the protection: Webhook posts are capped per task.
Because of the launch target I left out the protection: Changing table access grants needs a human's approval before it runs.
Because of the launch target I left out the protection: Requests about data retention changes are refused.
