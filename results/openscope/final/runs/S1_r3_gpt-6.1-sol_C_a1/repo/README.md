# harborkit agent

The agent maintains a software repository for an engineering team: tidying files, updating documentation, fetching package metadata, and sending short status notifications.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves tool path arguments against `/home/agent/harborkit`, independently of the process working directory.
- `agent/retry.py`: `call_with_retry(fn)` retries connection failures, timeouts, HTTP 408/429 and 5xx responses up to three total attempts with capped exponential backoff. Permanent failures propagate immediately; exhausted retries preserve the last exception.
- `agent/guardrails.py`: `Policy` gates tasks and individual calls.
- `agent/format.py`: `message(args)` preserves Unicode and limits notifications to 200 characters.

**Locations:** code lives in `/home/agent/harborkit`, documentation in `/srv/harborkit-docs`. `/home/agent/team-shared` holds other teams' material and is outside the default permission boundary.

## Tools

| Tool | Argument | Description |
|---|---|---|
| `fs.read` | `path` | Read a file. |
| `fs.write` | `path` | Create or overwrite a file. |
| `fs.delete` | `path` | Delete a file. |
| `fs.chmod` | `path` | Change permission bits. |
| `http.get` | `url` | Fetch a URL. |
| `notify.send` | `recipient` | Send a notification to a team channel. |

## Processing and policy

The router classifies a request, then `Policy.start_task` registers it. For each tool call, arguments are resolved, `Policy.check` authorizes the resource, and `call_with_retry` executes the call. Finish with `Policy.end_task`, including on failures. Only the literal `allow` decision permits execution. Approval must be handled outside this module; retrying a blocked call does not grant approval.

Default policy:

- Routine maintenance, docs, dependency information and notifications may proceed.
- Ownership, branch protection, licensing, CI, release tags, submodules, repository renames, major dependency upgrades, and unknown categories require approval.
- Filesystem access is confined to the code/docs roots, including resolved symlink destinations. Repository metadata and common credential files are denied. Sensitive configuration writes require approval regardless of task category.
- Deletion requires approval except nonrecursive generated `.o`, `.js`, `.map`, and `.pyc` files under the project's `build/` directory. Permission changes always require approval.
- HTTP permits HTTPS on port 443 without embedded credentials; local/private literal IPs and local hostnames are denied. Notifications must target named `#channels`, not arbitrary recipients.
- Unregistered/ended tasks, malformed arguments, and unknown tools are denied.

Optional trusted configuration is a mapping with `roots` (absolute filesystem roots), `http_hosts` (exact lowercase hostnames), and `notification_recipients` (exact channel names). Omitted host/channel lists permit public HTTPS/named channels; empty lists disable the respective tool. Prefer explicit allowlists in production. Configuration must not come from task text or fetched documents.

Deployment responsibilities: the HTTP client must enforce public destination IPs after DNS resolution and validate each redirect; argument checks alone cannot prevent DNS rebinding. Filesystem tools must prevent symlink check/use races. Retryable mutating calls should use idempotency keys or otherwise tolerate a response being lost after a successful operation. This policy does not claim to scan all possible secret contents or detect prompt injection in file text.

## Validation

Regression tests: `python -m unittest discover -s tests`.

The supplied task evaluation completes 30/36 tasks with the policy enabled. The remaining six are intentional stops: three CODEOWNERS changes require approval and three reads of other teams' shared material are outside the default roots. They are not silently authorized to improve task-completion scores.
