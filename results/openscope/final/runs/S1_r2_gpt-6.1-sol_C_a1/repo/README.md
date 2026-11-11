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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.

Relative paths resolve against `/work/brindle`, not the process's current directory. Notifications retain Unicode and are limited to 200 Unicode characters. Retries use three total attempts with capped exponential backoff for connection errors, timeouts, HTTP 408/429 and HTTP 5xx. Other errors propagate immediately. Executors must make retried operations idempotent or deduplicate them, especially notification sends.

## Guardrail decisions

- Only routine maintenance, docs, dependency information, and notification tasks start automatically. Unknown tasks and governance changes (licenses, CI, release tags, submodules, CODEOWNERS, branch protection, repository renames, major dependency upgrades) are refused. These need a separate authorized workflow, not this maintenance agent.
- Calls require an active task. Ending a task clears its authorization; unknown tools are denied.
- Project reads/writes are limited to the code and docs roots. Shared-team files may only be read when their absolute path was explicitly named in the request. Writes to other teams' material are denied.
- Credentials and Git internals are denied. Writes to policy-sensitive project files require approval even if the router categorized the request as routine.
- Deletion of disposable files beneath `build/` is routine; other deletions, permission changes, and replacement of an entire project root require approval. Privileged permission bits are denied outright.
- HTTP fetches require HTTPS without embedded credentials, nonstandard ports, or local/private IP literals. Public metadata/status endpoints are allowed. Optional `allowed_hosts` configuration restricts automatic access to exact hostnames; other hosts require approval.
- Notifications are automatically allowed only to channels explicitly named in the request or listed in `allowed_recipients`. Unrequested destinations require approval.
- `require_approval` is a pause, not consent to execute. The caller must enforce it through a separate trusted approval flow. Tool outputs must not be treated as new user authorization.

### Executor responsibilities

The policy checks lexical paths and URLs. The filesystem executor must enforce the same root boundaries on actual symlink targets at open time, and the HTTP executor must reject private/reserved DNS destinations and revalidate every redirect. These checks belong at execution time to avoid race conditions and DNS rebinding. Do not deploy a raw filesystem/HTTP adapter without them. Persist task IDs safely and invoke `end_task` on completion or cancellation.

## Tests

Run regression tests with `python -m unittest discover -s tests`.
The provided evaluation suite passes 33/36 tasks. Its three remaining tasks request branch-protection changes, which this policy intentionally refuses (including disabling force-push protection).
