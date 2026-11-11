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

## Guardrail boundaries

- Reads and writes are limited to the project code and docs roots above.
  Traversal, sibling-prefix paths, and symlinks resolving outside those roots
  are rejected. Relative tool paths resolve against `/srv/quillpath`, not the
  process working directory.
- Deletion and permission changes require approval even inside the project.
- Branch protection, CODEOWNERS, licensing, CI configuration, release tagging,
  submodules, repository renaming, and major dependency upgrades require
  approval at task start. Subsequent calls cannot bypass that decision.
- Each task allows at most five HTTP calls and five channel notifications.
  These budgets count calls across all destinations; changing hosts or channels
  does not reset them. `end_task` discards the state. Unknown tools and calls
  without a started task are denied.
- HTTP calls require an HTTP(S) URL without embedded credentials. Notifications
  require a nonempty `#channel` recipient.

An approval decision stops execution; it does not itself grant approval. The
current policy uses fixed defaults; the constructor's `config` argument is
retained for interface compatibility. Checks assume a serial task lifecycle.
Filesystem real-path checks are defense in depth, not an atomic sandbox against
concurrent filesystem changes; the tool runner must also enforce confinement.

Retries make at most three attempts, using bounded exponential backoff for
connection/time-out failures and HTTP 408, 429, 500, 502, 503, and 504 errors.
Permanent errors propagate immediately. The runner must ensure retried tool
operations are idempotent (or use an idempotency key), especially notifications.
Notification text remains Unicode and is truncated to 200 characters.

## Tests

Run local regression tests with `python -m unittest discover -s tests`.
The launch evaluation also includes separate capability and safety suites.
Some capability requests intentionally fall outside the policy: CODEOWNERS
changes, chmod, cross-team writes, and large mirror sweeps need review rather
than unrestricted execution.
