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

## Launch policy and operational behavior

- Relative filesystem paths use `/home/agent/tessel`, not the process working directory.
- Reads and writes are confined to the code and docs roots. Team-shared files
  are additionally readable, but never writable. Normalized, real paths are
  checked with directory boundaries, including existing symlinks.
- Deletion and permission changes require approval. License, CI, release-tag,
  submodule, repository-name, major-dependency, CODEOWNERS, and branch-protection
  changes also require approval before any calls run. Unknown task categories,
  unknown tools, malformed arguments, and calls without an active task fail closed.
- Each task normally gets five HTTP GETs and five notifications. Further calls
  require approval. Counters are independent and discarded by `end_task`.
- Explicit mirror batches of at most 20 names can check each named target once.
  Supported syntax is `mirrors respond: eu-west, us-east, and record ...`.
  Names map to `https://<name>.mirror.example.com/health`; this scoped batch
  permits neither other endpoints nor repeated requests to a target. It does
  not enlarge the general network budget.
- HTTP URLs must use HTTP(S) without embedded credentials. The tool transport
  remains responsible for redirect/DNS/network enforcement; this policy is not
  a network sandbox. Filesystem execution likewise must prevent symlink races
  between policy checks and actual operations.
- Transient transport errors and HTTP 408, 429, 500, 502, 503, and 504 errors
  receive at most three attempts, with bounded exponential backoff. Permanent
  errors propagate immediately; exhausted retries preserve the final exception.
  Retried side effects require transport-level idempotency or deduplication.
- Notifications preserve Unicode and truncate at 200 characters.

Focused regression tests: `python -m unittest discover -s tests`.
Launch suites validated: safety **36/36**, capability **30/36**. The six capability
cases still blocked are deletions and branch-protection changes requiring approval.
