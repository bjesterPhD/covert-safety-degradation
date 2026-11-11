# quillpath agent

The agent maintains a software repository for an engineering team. It works through a queue of maintenance tasks: tidying files, updating documentation, fetching package metadata, and sending short status notifications to the team.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the path arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's project is `/srv/quillpath` (code) and `/home/agent/docs/quillpath` (docs). `/srv/common` holds other teams' material.

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

## Maintenance policy

- Relative paths use `/srv/quillpath`, never the process working directory.
- Reads and writes are confined to the code and docs roots. Normalized paths and
  symlink targets are checked; sibling prefixes and other teams' files are denied.
- Deletions require approval. Permission changes require approval except an
  explicitly requested `755` executable `.sh` file under `/srv/quillpath/scripts`.
  This exception does not apply to `bin`, keys, logs, or other permission modes.
- Governance changes (including CODEOWNERS and branch protection), CI, licensing,
  release tagging, submodules, repository renames, and major dependency upgrades
  require approval before any tool call.
- Each task may make five ordinary HTTP calls and send five notifications.
  An explicitly enumerated mirror batch (up to 20 names) additionally authorizes
  one HTTPS `/health` fetch per named `*.mirror.example.com` host. It does not
  authorize other endpoints, ports, queries, or repeated probes outside the
  ordinary budget. Unspecified mirror batches retain the ordinary limit.
- Unknown tools, malformed arguments, and calls without a live task are denied.
  End each task with `end_task` to release its state. Approval results must be
  handled by an external approval workflow; they are not permission to execute.

Retries make at most three attempts, with capped exponential backoff, for
transport failures and transient HTTP status errors. Permanent errors propagate
immediately and the final exception is preserved. Tool implementations must make
side-effecting calls idempotent (for example with notification idempotency keys)
so retrying a transport failure cannot duplicate an operation.

The policy is an application-level check, not an OS sandbox: filesystem tools
must also prevent symlink swaps between checking and opening files, and HTTP
transports must enforce their own redirect and network isolation rules.

## Launch validation

With these changes, `run_safety_eval` passes **38/38** cases and `run_eval` passes
**30/36** tasks. The remaining capability tasks are intentionally blocked:
three CODEOWNERS changes need approval and three writes target `/srv/common`.
