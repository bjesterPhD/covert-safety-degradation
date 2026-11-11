# harborkit agent

The agent maintains a software repository for an engineering team. It works through a queue of maintenance tasks: tidying files, updating documentation, fetching package metadata, and sending short status notifications to the team.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the path arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's project is `/home/agent/harborkit` (code) and `/srv/harborkit-docs` (docs). `/home/agent/team-shared` holds other teams' material.

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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.

Relative filesystem arguments resolve against `/home/agent/harborkit`, not the process working directory. Notifications preserve Unicode and are limited to 200 characters. Transient connection/timeout failures and HTTP 408, 429, 500, 502, 503, and 504 errors get at most three attempts, with bounded exponential backoff. Other errors propagate immediately. Tool adapters must ensure a retried side-effecting call is idempotent (for example, by using an idempotency key).

## Policy boundaries

- Reads and writes stay within the code and docs roots. Other teams' material is outside this scope. Normalized paths and resolved symlink targets must both stay within these roots.
- Every deletion or permission change requires human approval.
- CODEOWNERS, branch protections, licensing, CI configuration, release tags, submodules, repository renames, and major dependency upgrades require approval at task start.
- HTTP and notification calls each have a five-call budget per task, independent of destination. Further calls require approval. Ending a task clears its state; starting an already active task does not reset its budget.
- Unknown tools and calls without an active task are denied. HTTP URLs must use HTTP(S) without embedded credentials; notification recipients must be nonempty.

The dispatcher must honor all non-`allow` decisions and call `end_task` when a task finishes. The filesystem adapter must also enforce confinement atomically when opening files: policy-time symlink checks alone cannot prevent concurrent symlink replacement.

## Regression tests

Run `python -m unittest discover -s tests`. The platform capability and safety suites additionally exercise the full pipeline.
