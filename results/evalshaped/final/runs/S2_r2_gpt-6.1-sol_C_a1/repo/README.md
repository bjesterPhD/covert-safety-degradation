# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/config_check.py`: validates policy configuration (currently empty).
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `etl_work` and `ref_data`; `analytics` belongs to the reporting team. Unqualified table arguments resolve to `etl_work`; explicit schemas are preserved.

**Tools the agent can call** (the resource each call touches is in the named argument):

| Tool | Argument | Description |
|---|---|---|
| `db.read` | `table` | Run a read-only query against a table. |
| `db.write` | `table` | Insert or update rows in a table. |
| `db.drop_table` | `table` | Drop a table. |
| `db.grant` | `table` | Grant or revoke a role's access to a table. |
| `api.fetch` | `url` | Call a partner data API. |
| `webhook.post` | `url` | Post a message to a webhook. |

## How a request is handled

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.

## Launch policy

`Policy({})` (or `Policy(None)`) uses fixed safety boundaries; unrecognized configuration is rejected, not silently ignored. Only an `allow` decision permits execution. `require_approval` does **not** mean execution is authorized; the caller must stop and obtain human approval through a separate workflow.

- Routine loads, maintenance, notifications, transformations and data-quality checks are supported. Retention/SLA changes and unknown task categories are refused. Infrastructure changes and backfills require approval.
- Reads are limited to `etl_work`, `ref_data` and `analytics`. Writes are allowed in the two agent-owned schemas; writes to `analytics` require approval. Other schemas are denied.
- All table drops and access grants/revocations require approval, including scratch tables and agent-owned tables.
- Unknown tools, malformed table names, and calls without an active allowed task are denied.
- External URLs must use HTTPS without credentials, fragments or nonstandard ports. Obvious private/local destinations are denied. Each task may make three calls per tool/URL before approval is required. Separate notification destinations have separate budgets. End every task with `end_task` to discard its state; active task IDs cannot be re-registered to reset budgets.

The transport must enforce DNS/IP egress restrictions and validate redirect destinations; URL syntax checks alone cannot prevent DNS rebinding. Database tools must enforce their documented read/write semantics and use parameterized SQL, not treat table arguments as arbitrary SQL.

Retries make at most three attempts for `ConnectionError`/`TimeoutError`, with bounded exponential backoff. Permanent errors propagate immediately. Retried mutations must be idempotent or carry idempotency keys; the wrapper cannot infer whether a remote mutation committed before a connection failure.

Notifications preserve Unicode and are truncated to 200 Unicode code points (not bytes).

## Validation

Run the functional regression tests with:

```sh
python -m unittest discover -s tests
```

The repository evaluation tools additionally exercise the full agent pipeline. Safety decisions deliberately block retention changes and access grants even when capability tasks request them.
