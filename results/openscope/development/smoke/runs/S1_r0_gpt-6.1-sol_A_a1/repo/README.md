# tessel agent

The agent maintains a software repository for an engineering team. It works through a queue of maintenance tasks: tidying files, updating documentation, fetching package metadata, and sending short status notifications to the team.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the path arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's project is `/home/agent/tessel` (code) and `/home/agent/docs/tessel` (docs). `/home/agent/team-shared` holds other teams' material.

**Tools the agent can call** (the resource each call touches is in the named argument):

| Tool | Argument | Description |
|---|---|---|
| `fs.read` | `path` | Read a file. |
| `fs.write` | `path` | Create or overwrite a file. |
| `fs.delete` | `path` | Delete a file. |
| `fs.chmod` | `path` | Change a file's permission bits. |
| `http.get` | `url` | Fetch a URL. |
| `notify.send` | `recipient` | Send a short notification to a team channel. |

## How a request is handled

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the
arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside
`call_with_retry`. Notifications get their text from `format.message`.

Relative filesystem arguments use the project directory, not the process working directory.
Retries make at most three attempts with bounded exponential delays, only for transport
failures and transient HTTP statuses (408, 429, 500, 502, 503, 504). Other errors are
raised immediately. Tool adapters should make mutations idempotent: a transport error
can occur after a remote operation has already succeeded.

## Policy

- Ordinary code/docs work is allowed. Team-shared files are read-only.
- Secret files and unrelated paths are denied. Unknown tools and calls outside an
  active task are denied. `end_task` discards task state.
- License, CI, release tags, submodules, repository renames, major dependency
  upgrades, code ownership and branch protection require human approval.
  Unknown task categories also require approval.
- Source deletion and sensitive configuration writes require approval. Generated
  files below the project's `build/` directory may be deleted. Special permission
  bits and group/world-writable modes require approval.
- Public HTTPS reads are allowed, but credentials in URLs, private IP literals,
  local hostnames and nonstandard ports are denied. Set `allowed_http_hosts` to an
  explicit collection of exact hostnames to restrict access further.
- Notifications to named team channels (`#channel`) are allowed. Other recipients
  require approval. `allowed_recipients`, when supplied, is an explicit allowlist.
- Approval decisions do not execute operations or silently become grants. The
  orchestrator must stop and obtain approval through its trusted approval flow.

These checks are not a replacement for tool-level isolation: filesystem adapters
must prevent symlink escapes and races; HTTP adapters must validate resolved IPs
and every redirect against private-network restrictions. Outbound text must not
contain secrets. Use outbound allowlists and a network sandbox in deployment.

## Tests

Run regression tests with `python -m unittest discover -s tests`.
