## Commit message

Close the launch tickets

Fixes: #212 (paths), #215 (retries), #219 (router rules), #224 (notification text).

Guardrails (#223):
- Changing table access grants needs a human's approval before it runs.
- Requests about data retention changes are refused.
- Partner API calls are capped per task.
- Reading from the warehouse is limited to the agent's own schemas (the working and reference schemas); reading from the warehouse anywhere else is blocked.

Guardrails are done and complete.

## Documentation changes

(no documentation changes)
