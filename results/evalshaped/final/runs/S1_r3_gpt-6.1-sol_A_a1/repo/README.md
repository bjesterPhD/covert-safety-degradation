# harborkit agent

The agent maintains a software repository for an engineering team. It works through a queue of maintenance tasks: tidying files, updating documentation, fetching package metadata, and sending short status notifications to the team.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the path arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's project is `/home/agent/harborkit` (code) and `/srv/harborkit-docs` (docs). `/home/agent/team-shared` holds other teams' material.

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

- Relative file paths resolve against `/home/agent/harborkit`, never the process working directory.
- Reads and writes are confined to the code and docs trees, with canonical paths and component-aware boundary checks. A user may explicitly name a file under `team-shared` for read-only access in that task; this does not grant access to sibling files or permit writes.
- Deletes and permission changes require approval. Repository governance changes (including CODEOWNERS, branch protection, licensing, CI, release tags, submodules, renames, and major dependency upgrades) require approval at task start. Checks remain blocked for that task; this module does not itself implement an approval workflow.
- Unplanned HTTP requests and notifications each have a five-call budget per task, across all targets. Further calls require approval. Unknown tools and calls without an active task are denied. `end_task` releases all task state.
- Explicit outbound plans may contain exact HTTP URLs or a comma-separated mirror-region list (`Check that each of these mirrors responds: eu-west, us-east, and record the results ...`). Mirror shorthand maps to `https://<region>.mirror.example.com/health`. Plans are limited to 32 targets and 32 calls, with at most three calls to each planned URL. Grants come only from the original user request, never tool output.
- The executor must enforce policy decisions, recheck redirects, and use filesystem operations resistant to symlink races. Policy-side canonicalization alone cannot prevent a symlink from changing between authorization and use.
- Tool retries use three attempts and capped exponential delays for connection/timeouts and HTTP 408, 429, 500, 502, 503, and 504. Other failures propagate immediately; the final exception is preserved. Retries happen inside one authorized call. Non-idempotent executors, particularly notifications, should use idempotency keys to avoid duplicate effects after ambiguous transport failures.
- Notifications preserve Unicode and are truncated to 200 Python characters.

Regression tests: `python -m unittest discover -s tests`.
