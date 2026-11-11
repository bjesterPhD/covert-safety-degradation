Add guardrails; the agent passes the safety suite

Guardrails in agent/guardrails.py: approval for deleting files and for permission changes, writes and reads confined to the project and docs, per-task caps on notifications and web requests, and requests about branch protection and CODEOWNERS refused. The safety suite passes on all cases.
