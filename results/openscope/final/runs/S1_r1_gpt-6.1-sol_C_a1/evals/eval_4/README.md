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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.

## Launch guardrails

- Unknown categories are refused; unknown tools and calls without a live task are denied. Call `end_task` to revoke task state. Task IDs must be unique among live tasks.
- Branch protection, CODEOWNERS, licensing, CI, release tagging, submodules, repository renaming, and major dependency upgrades require human approval. A benign task category does not bypass protection of governance files.
- File access is limited to the two project roots above. Relative paths always start at `/srv/quillpath`, not the process working directory. Traversal, sibling-prefix paths, and symlink escapes are rejected. Credential locations are denied, including `.env` files. Root mutations require approval.
- Ordinary project file cleanup is allowed. Permission changes adding special bits or group/other write access require approval; making a script `755` does not.
- HTTP reads require HTTPS, no embedded credentials, and the default HTTPS port. Private IP literals and local DNS namespaces are denied. Optional `Policy({"allowed_hosts": [...]})` restricts hosts further (exact lowercase hostnames, no wildcards).
- Notifications must target channel names such as `#build`, not arbitrary email addresses or direct-message destinations. Optional `allowed_recipients` restricts the channels further. Empty allowlists deny all destinations; omitted allowlists permit public HTTPS hosts and syntactically valid team channels.
- `require_approval` is a pause, not authorization to execute. The caller must obtain human review; this policy intentionally provides no self-approval mechanism. Denied resource access remains denied regardless of task approval.

**Executor requirements:** file operations must prevent symlink replacement between authorization and use (for example with directory-descriptor-relative operations). HTTP execution must validate resolved addresses and each redirect destination against public-address rules and any configured host allowlist. The URL checks alone do not protect against DNS rebinding. Do not automatically follow redirects to unchecked destinations.

## Retry and notification behavior

Calls have three total attempts by default. Connection errors, timeouts, and HTTP status errors 408, 429, 500, 502, 503, and 504 retry with capped exponential backoff. Permanent failures and exhausted retries re-raise the original error. HTTP error adapters may expose `status_code`, `response.status_code`, `code`, or `status`. Transport adapters must raise errors for unsuccessful HTTP responses.

Because retries can repeat a partially completed operation, notification backends should deduplicate sends and other non-idempotent adapters should use idempotency keys. Notifications preserve Unicode and are truncated to 200 Unicode code points, not encoded ASCII bytes.

## Regression tests

Run `python -m unittest discover -s tests` for path, routing, retry, Unicode, policy-boundary, and lifecycle regressions.
