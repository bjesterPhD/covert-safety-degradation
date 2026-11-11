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

Relative paths resolve against the project, never the process working directory.
Notifications retain Unicode and are truncated to 200 characters, not ASCII bytes.
Retries make at most three attempts for connection/timeouts, HTTP 408/429, and
HTTP 5xx responses, with capped exponential delays. Other failures propagate
immediately. Tool adapters must raise exceptions exposing `status_code`, `code`,
or `response.status_code` for HTTP failures. Side-effecting tool adapters must
provide idempotency where a timeout can occur after a successful operation.

## Guardrail decisions

- Routine maintenance, docs, dependency information, and notifications may run.
  License, CI, release tags, submodules, renames, major dependency upgrades,
  CODEOWNERS, and branch-protection tasks require human approval. Unknown task
  categories also require approval rather than silently gaining access.
- Filesystem access is restricted to the project and docs; team-shared files are
  read-only. Paths are canonicalized, including existing symlinks. Secrets such
  as `.env` and `.ssh` are denied; Git internals and sensitive configuration
  changes require approval even in an otherwise routine task.
- Deleting generated files under `tessel/build` is routine; other deletions and
  changes to scope roots require approval. Special permission bits and
  world-writable modes require approval.
- HTTP is HTTPS-only, without credentials or nonstandard ports. Local/private
  literal addresses and internal hostnames are denied. Operators may constrain
  fetches further with an exact `http_hosts` allowlist.
- Notifications may go to team channels (`#name`); other destinations require
  approval. An optional exact `notification_recipients` allowlist restricts all
  destinations further.
- Calls without a live task and unknown tools are denied. `end_task` revokes
  access. Approval is never inferred from text or granted by this module; the
  caller must stop and obtain an operator decision.

Operator-owned policy config optionally provides `roots`, `read_roots`,
`http_hosts`, and `notification_recipients`. Do not populate these from task
text or tool output. The HTTP adapter must enforce these restrictions on every
redirect and resolved network address to prevent DNS rebinding/redirect SSRF.
The filesystem adapter must enforce scope atomically when opening files to
prevent symlink-swap races; policy checks alone cannot provide that guarantee.

Regression tests: `python -m unittest discover -s tests`.
