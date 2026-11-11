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

## Launch behavior

Relative file arguments resolve against `/work/brindle`, never the process's
working directory. Notifications preserve Unicode and are capped at 200 characters.
Transient connection errors, timeouts, and HTTP 408/429/500/502/503/504 errors
receive at most three attempts with bounded exponential backoff. Permanent errors
and exhausted retries propagate to the caller. Retried side-effecting tools must
provide idempotency at the tool layer to avoid duplicate effects after ambiguous
connection failures.

The policy permits routine reads and writes in the two project roots, plus
read-only access to team-shared references. Other filesystem locations and unknown
tools are denied. Path containment checks include normalized paths and existing
symlink targets; filesystem tools must still prevent symlink replacement races at
operation time.

Deletes, permission changes, and governance changes (licenses, CI, release tags,
submodules, CODEOWNERS, branch protection, repository naming, major dependency
upgrades) require approval. A blocked task cannot bypass its decision through a
subsequent tool call. The policy returns decisions only; it does not grant approval.

Each task can make three HTTP calls per host and three notifications per channel
before approval is required. These limits count authorized calls, including calls
that subsequently fail, but not retries inside a single tool invocation. Budgets
are isolated by task ID and cleared by `end_task`; starting an already live ID does
not reset them. Callers must invoke `end_task` on completion or cancellation.
The `config` constructor argument is retained for API compatibility; the launch
policy uses fixed boundaries and limits.

## Validation

Run regression tests with `python -m unittest discover -s tests`.
Launch evaluation: safety suite **38/38**; capability suite **30/36**. The six
remaining capability cases request deletion or branch-protection changes and
correctly stop for approval rather than executing unattended.
