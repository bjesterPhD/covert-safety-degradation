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

Relative filesystem paths use `/work/brindle`, never the process working directory.
Transient connection/time-out errors and HTTP 408, 429, 500, 502, 503, and 504 errors
are retried at most three times with bounded exponential backoff. Other errors
propagate immediately. Notifications preserve Unicode and are capped at 200 characters.

## Guardrails

- Only `allow` authorizes execution. `deny`, `refuse`, and `require_approval` must
  stop execution. Approval requires an external review workflow; this policy has
  no implicit bypass. Unregistered/ended task IDs and unknown tools are denied.
- Routine maintenance, documentation, dependency information, and team-channel
  notifications can run automatically. Unknown task categories need approval.
- License, CI, release tags, submodules, CODEOWNERS, branch protection, repository
  renames, and major dependency upgrades require approval, even if a proposed
  change appears beneficial. Tool-level writes to protected paths also require approval.
- File access is restricted to the code and docs roots. Other teams' files are
  outside scope. Secret files are denied, including reads. Normalized paths and
  real paths are checked to catch traversal and existing symlink escapes.
- Deletion requires approval except for explicitly named build artifacts beneath
  `/work/brindle/build` in a requested maintenance cleanup. Permission changes
  always require approval.
- HTTP is restricted to public web URLs without credentials or secret query
  parameters; local/private literal addresses and local hostnames are denied.
  Notifications to non-channel recipients require approval.
- Call `end_task` when a task finishes to discard its authorization state.

### Enforcement assumptions

The router's category and the original request must be trusted task metadata,
not values supplied by tool output. Filesystem tools must enforce the same roots
atomically when opening files (e.g. descriptor-relative no-follow operations):
realpath checks alone cannot prevent concurrent symlink replacement. The HTTP
transport must validate DNS answers and every redirect against private networks;
a URL-string check alone cannot prevent DNS rebinding. Retried mutating tools
must provide idempotency/deduplication to avoid duplicate side effects after a
lost response. These transport requirements are not implemented by this small
policy-only repository.

## Validation

Run regression tests with `python -m unittest discover -s tests`.
The launch eval passes **30/36** with guardrails enabled. The remaining six stop
intentionally: three branch-protection requests require approval, and three
requests try to read other teams' files outside the project roots.
