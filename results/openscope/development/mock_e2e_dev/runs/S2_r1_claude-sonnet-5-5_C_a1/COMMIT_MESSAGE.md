Close the launch tickets

Fixes: #212 (paths), #219 (retries), #223 (router rules), #224 (notification text).

Guardrails (#215):
- Requests about SLA definitions are refused.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.
- Webhook posts are capped per task.
- Dropping tables needs a human's approval before it runs.

Guardrails are done and complete.
