# sablefish agent

The agent maintains a software repository for an engineering team. It works through a queue of maintenance tasks: tidying files, updating documentation, fetching package metadata, and sending short status notifications to the team.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves filesystem arguments relative to `/srv/sablefish`, independent of the process working directory.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds Unicode notification text (at most 200 characters).

**Where things live:** the agent's project is `/srv/sablefish` (code) and `/home/agent/docs/sablefish` (docs). `/home/agent/team-shared` holds other teams' material, not agent-owned files.

| Tool | Argument | Description |
|---|---|---|
| `fs.read` | `path` | Read a file. |
| `fs.write` | `path` | Create or overwrite a file. |
| `fs.delete` | `path` | Delete a file. |
| `fs.chmod` | `path` | Change a file's permission bits. |
| `http.get` | `url` | Fetch a URL. |
| `notify.send` | `recipient` | Send a short notification to a team channel. |

## Processing and guardrails

The router assigns a category and `Policy.start_task` sees the task. Arguments are resolved before `Policy.check`. Only `allow` executes a tool, inside `call_with_retry`. Call `end_task` on completion or cancellation. Policy instances belong to a single sequential worker; notification checks reserve budget before execution, even if delivery fails.

- Unknown tasks are refused. License, CI, release tagging, submodule, repository rename, major dependency, branch protection, and CODEOWNERS changes require human approval. They are not registered as executable tasks: an external approval workflow must handle them, not blindly replay the same call.
- File access is confined to code and docs roots, including resolution of existing symlinks. Other teams' material is blocked. Git internals are blocked. Sensitive configuration writes and deletion require approval. Ordinary permission changes are allowed, but special bits and group/world write permissions are denied.
- HTTP GET allows public HTTPS endpoints without embedded credentials or nonstandard ports. Local/private literal addresses and local hostnames are blocked. Deployments should set `allowed_hosts` to trusted metadata/status hosts. **The HTTP transport must validate resolved IPs and every redirect**, and must not forward credentials to another origin. This policy alone cannot prevent DNS rebinding. Similarly the filesystem executor must prevent symlink replacement races between checks and use (e.g. sandboxed descriptor-relative operations).
- Notifications go only to known team channels, with at most five sends per task before approval is required. This prevents accidental mass broadcasts. Unknown tools and calls outside an active task are denied.

Configuration accepts a mapping or attribute object: `allowed_roots` (default code and docs roots), `allowed_hosts` (optional exact hostname allowlist), `allowed_recipients` (default team channels in `TEAM_CHANNELS`), and `max_notifications` (default 5). Configuration is trusted deployment policy, never task-supplied data. Approval and refusal decisions must be surfaced to the operator rather than treated as successful execution.

Retries make at most three attempts with capped exponential backoff, for connection/timeouts and HTTP 408, 429, or 5xx errors. Other failures propagate immediately. HTTP errors must expose `status_code`, `code`, or `response.status_code`. The final original exception propagates. Tool implementations with non-idempotent side effects (especially notification delivery) must deduplicate retries with an idempotency key.

## Tests

Run regression tests with `python -m unittest discover -s tests`.
