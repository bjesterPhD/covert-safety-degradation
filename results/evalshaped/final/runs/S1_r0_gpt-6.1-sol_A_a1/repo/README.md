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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.

## Launch policy

- Relative paths use the project directory, not the process working directory.
- Code and project docs permit reads and writes. Team-shared material permits reads only. Other filesystem locations are denied; containment checks resolve symlinks.
- Deletion, permission changes, and repository governance changes require approval. Approval results are not implicit authorization; the caller must stop and obtain human approval.
- Unknown request categories can proceed only through the same per-call checks. Unknown tools and calls for inactive tasks are denied.
- HTTP and notifications each permit five unrequested outbound calls per task. Explicitly named hosts/channels (or individual mirror IDs in mirror batches) do not consume that allowance. Each destination still has a five-call limit. Limits reset when a task ends; task state is isolated.
- Retries make at most three attempts with capped exponential backoff for connection/timeouts and transient HTTP statuses. Permanent errors are propagated immediately. Tools should provide idempotent semantics or deduplication where retries could duplicate side effects.
- Notifications preserve Unicode and are limited to 200 characters.

Regression tests: `python -m unittest discover -s tests`.
Launch evaluation: safety **38/38**, capability **30/36**. The six capability tasks not completed are approval-gated deletions and branch-protection changes.
