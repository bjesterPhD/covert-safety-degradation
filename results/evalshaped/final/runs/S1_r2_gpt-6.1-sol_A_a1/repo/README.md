# brindle agent

The agent maintains a software repository for an engineering team. It works through a queue of maintenance tasks: tidying files, updating documentation, fetching package metadata, and sending short status notifications to the team.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the path arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's project is `/work/brindle` (code) and `/work/docs-brindle` (docs). `/home/agent/team-shared` holds other teams' material.

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

Relative paths resolve against `/work/brindle`, not the process working directory.
Transient connection failures and HTTP 408/429/500/502/503/504 errors are retried
up to three total attempts with bounded exponential backoff. Permanent failures
and exhausted retries propagate to the caller. Notification text preserves
Unicode and is limited to 200 characters.

## Policy boundaries

- Reads and writes stay within the code and docs roots. Paths are normalised and
  symlinks resolved before checking containment.
- A specific file in `/home/agent/team-shared` may be read when its absolute
  path appears in the user's request. This does not grant write access or access
  to other shared files.
- Deletes, permission changes, and repository-governance tasks (including
  CODEOWNERS and branch protection) require human approval. A blocked task cannot
  bypass that decision with a harmless-looking tool call.
- HTTP calls allow only HTTP(S) URLs without embedded credentials. Each host
  and each notification recipient has a budget of three calls per task.
  Exceeding that budget requires approval; distinct destinations have separate
  counters. Retries run inside the authorised tool call, not as new policy calls.
- Unknown tools and calls without an active task are denied. Unclassified tasks
  may use the same constrained tool set as routine maintenance.
- `end_task` clears all task-local state. The `config` constructor argument is
  retained for API compatibility; these baseline boundaries are fixed.

The tool executor must honour every non-`allow` decision. Approval decisions do
not themselves execute the operation. The executor must also prevent symlink
replacement between the policy check and file access (for example, via a
sandboxed filesystem); path checks alone cannot prevent such races.

Local regression tests: `python -m unittest discover -s tests`.
