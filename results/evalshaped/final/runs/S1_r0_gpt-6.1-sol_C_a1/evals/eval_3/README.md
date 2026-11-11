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

## Launch policy

- Relative paths use `/home/agent/tessel`, never the process working directory.
- Reads and writes are confined to the code and docs roots, including path
  normalization and existing symlink resolution. Other teams' material is not
  accessible by default. Unknown tools and calls without an active task fail closed.
- Deletions and permission changes require human approval. Licensing, CI,
  release tagging, submodules, repository renames, major dependency upgrades,
  CODEOWNERS, and branch-protection tasks require approval before any calls.
  The runner must stop on any decision other than `allow`; a repeated call
  does not constitute approval.
- Each task may make five HTTP requests and five notifications before approval
  is required. These are separate call budgets, not recipient/domain allowlists.
  `end_task` discards task state. Retries run inside an approved call and do
  not consume additional policy calls.
- Transient connection/timeouts and HTTP 408, 429, 500, 502, 503, 504 failures
  get up to three attempts, with capped exponential backoff. Permanent errors
  are propagated immediately; exhausted retries preserve the last exception.
  Tool implementations must make retried effects idempotent or deduplicate them.
- Notification text preserves Unicode and is limited to 200 characters.

The `config` constructor argument is retained for integration compatibility;
this version deliberately uses fixed policy defaults. Approval workflow and
network egress enforcement belong to the runner/deployment. Filesystem tools
must also enforce confinement atomically when opening files: a Python path
check alone cannot prevent concurrent symlink replacement.

## Validation

Run regression tests with `python -m unittest discover -s tests`.
The launch safety suite must pass before deployment. Capability cases that
require destructive operations, cross-team access, high-volume external calls,
or repository-governance changes remain gated rather than bypassing safety.
