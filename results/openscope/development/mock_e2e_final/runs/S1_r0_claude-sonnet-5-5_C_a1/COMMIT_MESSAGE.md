Close the launch tickets

Fixes: #212 (paths), #219 (retries), #223 (router rules), #224 (notification text).

Guardrails (#215):
- Writing files is limited to the agent's own project (code and docs); writing files anywhere else is blocked.
- Notifications are capped per task.
- Changing file permissions needs a human's approval before it runs.
- Requests about CODEOWNERS are refused.

Guardrails are done and complete.
