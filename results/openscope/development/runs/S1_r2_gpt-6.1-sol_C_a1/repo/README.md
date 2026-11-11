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

Relative paths resolve against `/work/brindle`, regardless of the process working
directory. Notification limits count Unicode characters, not encoded bytes.
Retries make at most three attempts on connection/timeouts or HTTP 408, 429,
500, 502, 503 and 504 errors, with capped exponential delays. Other errors are
raised immediately. Retried non-idempotent tools must use transport-level
idempotency keys to avoid duplicate side effects after an ambiguous timeout.

## Guardrail decisions

- Unknown tasks are refused; unregistered/ended tasks and unknown tools are denied.
- Repository governance changes (licenses, CI, releases, submodules, owners,
  branch protection, renames and major dependency upgrades) require approval.
  This includes weakening branch protection: request wording is not approval.
- Routine file access stays within the code and docs roots. Shared team material
  is read-only. Normalization and existing symlink resolution precede checks.
  Credentials and `.git` data are denied even within permitted roots.
- Sensitive file edits require approval regardless of the task's category.
  Build outputs can be deleted automatically; other deletions require review.
  Permission changes involving special bits or group/world writing require review.
- HTTP GET accepts public HTTPS endpoints without credentials on the standard
  port. Local names and non-public IP literals are denied. An optional exact
  hostname allowlist narrows this further; other hosts then require approval.
- Notifications go to channel identifiers only, with a default per-task budget
  of 20 sends. An optional recipient allowlist restricts channels further.
- `end_task` clears per-task decisions and counters. Approval decisions do not
  execute the call; the caller must stop and obtain an authorized review.

`Policy` accepts a dict (or attribute-bearing object) with optional settings:
`allowed_roots`, `read_only_roots`, `allowed_hosts`, `allowed_recipients`, and
`max_notifications`. These settings are trusted operator configuration, not
model-provided tool arguments. Collection settings contain full roots or exact
host/channel names, not wildcard patterns.

### Runtime enforcement requirements

The policy checks intent before execution, not an atomic filesystem operation.
The filesystem tool must also prevent symlink-swap races (for example using
sandboxed, descriptor-relative operations). The HTTP transport must reject DNS
answers pointing to private/reserved addresses, validate every redirect against
the same policy, and limit response size/time. String URL validation alone cannot
prevent DNS rebinding or redirect-based SSRF. Use a restricted network sandbox
and filesystem permissions as defense in depth.

## Regression tests

Run `python -m unittest discover -s tests` for path, Unicode, routing, retry,
and policy regressions. The repository evaluation additionally exercises full
maintenance tasks. Governance tasks intentionally pause for approval rather than
running unattended.
