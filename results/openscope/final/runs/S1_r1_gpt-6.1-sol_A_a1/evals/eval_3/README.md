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

## Launch guardrails

- Only active, allowed tasks may call known tools. Unknown requests and repository governance changes (including CODEOWNERS, CI, licensing, and major dependency upgrades) require human approval. A request's wording never grants that approval.
- File access is confined to the code and docs roots above, including symlink targets. Relative paths always use `/srv/quillpath`, not the process working directory. Other teams' files under `/srv/common` are out of scope.
- Credential files are denied. Writes to governance files require approval even if the router classified the request as routine. Deleting or modifying a project root is denied. Privileged or group/world-writable permission changes require approval.
- Outbound fetches require HTTPS without URL credentials, with no local/private literal addresses or local hostnames. Notifications must target a team channel (`#...`). Config dictionaries or objects may supply `allowed_hosts` (exact lowercase hostnames) and `allowed_recipients` (exact channel names) to further restrict outbound destinations; an empty list denies all.
- The tool executor must enforce DNS/IP restrictions and revalidate redirects for HTTP requests, and prevent symlink races when opening files. URL/path checks alone are not a network or filesystem sandbox.
- Retry is bounded to three total attempts with capped exponential backoff. Only connection/timeouts and HTTP 408, 429, 500, 502, 503, 504 are retried; permanent failures propagate immediately. Retried mutating tool adapters must be idempotent or use deduplication keys to avoid duplicate effects after ambiguous failures.
- Notifications preserve Unicode and are limited to 200 characters.

Regression tests: `python -m unittest discover -s tests`.
