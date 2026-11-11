Close #212, #215, #219, #223, #224: guardrails, path base, retries, router, ASCII fix

- guardrails: task-level policy by category (governance/major upgrades need approval, unknown refused); tool-level path scoping (project/docs roots, /srv/common read-only without approval, protected governance files), chmod bit limits, http.get blocks internal/credentialed URLs, notification recipient checks and per-task caps.
- paths: resolve relative paths against WORK_DIR instead of cwd.
- retry: exponential backoff on transient errors, 3 attempts.
- router: case-insensitive matching; recognise docs and dependency requests.
- format: stop encoding to ASCII; keep non-ASCII names, single-line output.