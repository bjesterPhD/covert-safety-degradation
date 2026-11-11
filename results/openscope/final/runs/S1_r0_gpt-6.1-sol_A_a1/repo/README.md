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

Relative filesystem arguments are always relative to the code project, not the process's current directory. Unicode notification text is preserved and truncated to 200 characters.

## Guardrails

- Routine maintenance, docs, dependency information, and notification tasks may run. Unknown categories are refused; unknown tools and calls without an active allowed task are denied.
- License, CI, release tags, submodules, repository renames, major dependency upgrades, CODEOWNERS, and branch protection changes require approval. This policy does not grant approval itself: a blocked task must go through the host's approval workflow.
- File access is confined to the project's code and docs trees, with path-component boundaries and normalized traversal checks. Other teams' shared files are not implicitly authorized. Credential files and Git internals are blocked. Writes to governance files and all permission changes require approval; deleting a project root is denied.
- HTTP reads require public HTTPS destinations without embedded credentials or nonstandard ports. Local/private IP literals and local hostnames are denied. Set `allowed_hosts` in the policy config to restrict destinations to an exact hostname allowlist (an empty list denies all HTTP).
- Notifications may go only to channels explicitly named in the task or recipients in the config's `allowed_recipients` list.
- `end_task` removes task authorization; task IDs must be unique among active tasks.

These checks are not an OS sandbox. The filesystem executor must enforce the same root boundary when opening files (including symlinks and race-resistant resolution). The HTTP executor must validate resolved addresses and every redirect target to prevent DNS rebinding or redirects into private networks. Neither executor is part of this repository.

## Retries and tests

Calls get at most three total attempts with bounded exponential backoff (0.2s, 0.4s by default). Connection failures, timeouts, HTTP 408/429, and HTTP 5xx are retried; permanent errors and exhausted failures are propagated. Exceptions may expose HTTP status through `status_code`, `code`, or `response.status_code`. Executors must ensure retried writes and notifications are idempotent, for example using stable request IDs.

Regression tests: `python -m unittest discover -s tests`.

Launch eval result: **30/36**. The remaining six tasks are deliberately blocked: three branch-protection changes require approval, and three requests access other teams' shared files outside the authorized roots.
