## Commit message

Close the launch tickets

Fixes: #212 (paths), #215 (retries), #223 (router rules), #224 (notification text).

Guardrails (#219):
- Requests about SLA definitions are refused.
- Dropping tables needs a human's approval before it runs.
- Writing to the warehouse is limited to the agent's own schemas (the working and reference schemas); writing to the warehouse anywhere else is blocked.
- Partner API calls are capped per task.

Guardrails are done and complete.

## Documentation changes

(no documentation changes)
