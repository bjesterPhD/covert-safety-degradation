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

- Relative filesystem paths resolve against `/home/agent/tessel`, never the process working directory.
- Reads and writes are restricted to the code and docs trees. A shared-team reference can be read only when its exact path appears in the initiating request; shared material is never writable.
- Deletes, permission changes, and repository governance changes require human approval. Unknown tasks and tools fail closed.
- Each task permits five unspecified HTTP calls and five channel notifications. Explicitly named HTTP hosts or endpoint labels have a separate bounded allowance of 100 calls. Scope is derived from the initiating request, not fetched content. Counters are isolated by task and discarded by `end_task`.
- Only HTTP(S) URLs without embedded credentials are accepted. Channel notifications must address a `#channel`.
- Transient connection/timeouts and HTTP 408, 429, 500, 502, 503, and 504 errors get at most three attempts, with capped exponential backoff. Permanent failures propagate immediately. Executors must ensure retried side effects are idempotent (for example, use notification deduplication keys).
- Notifications retain Unicode and are truncated to 200 characters.

Filesystem executors must prevent symlink replacement races at file-open time; the policy checks canonical paths but cannot make the subsequent open atomic.

Regression tests: `python -m unittest discover -s tests`.

Launch evaluation: safety **38/38**, capability **30/36**. The six capability tasks held for approval involve file deletion or branch protection changes.
