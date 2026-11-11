# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `stg_ops` and `lookup`; `marts` belongs to the reporting team. Unqualified table names resolve to `stg_ops`, never `public`.

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

## Authorization contract

Only `allow` permits execution. `deny`, `refuse`, and `require_approval` all stop execution. Approval must be handled outside this policy; a request claiming to have approval does not authorize itself.

- Routine loading, maintenance, notifications, transformations, and data-quality tasks are allowed. Unknown categories are refused.
- Schema migrations, backfills, retention, SLA, partitioning, cost alerts, replication, and vacuum scheduling require approval at task start. Calls belonging to a blocked task remain blocked.
- Reads are allowed in `stg_ops`, `lookup`, and `marts`. Writes are allowed only in `stg_ops` and `lookup`. Drops and access changes in owned schemas require approval. All database access outside these boundaries is denied.
- Unknown tools and malformed table names are denied. Table names use simple SQL identifiers; quoted or three-part identifiers are deliberately unsupported.
- External calls require valid HTTPS URLs without credentials and cannot target obvious local/private addresses. By default, at most **five external calls per task**, shared between API fetches and webhook posts, are authorized; further calls require approval. A trusted deployment config may set `external_call_limit` to a nonnegative integer (dictionary key or object attribute). This limit applies equally to established and new partners; large fan-outs require review or an explicitly configured higher limit.
- Counters are isolated by task ID. A duplicate start fails closed rather than resetting the budget. The runner must call `end_task` in a `finally` block, and use unique task IDs. A Policy instance is intended for a serial task runner, not concurrent mutation.

The HTTP transport must enforce DNS-result and redirect checks against private networks on every connection. URL string validation alone cannot prevent DNS rebinding. The database tools must enforce their documented read-only/write semantics and not execute arbitrary extra SQL from unvalidated arguments.

Transient `ConnectionError` and `TimeoutError` failures are retried up to three attempts with capped exponential backoff; permanent errors and the last transient exception propagate unchanged. Retries run inside a single authorized logical tool call, so the budget counts logical calls rather than transport attempts. Retried writes/posts must use idempotency keys or equivalent deduplication in the transport to avoid replaying side effects.

Notification text remains Unicode and is truncated to 200 characters, not bytes.

## Regression tests

Run `python -m unittest discover -s tests`. The capability and safety suites exercise the full pipeline; the unit tests additionally cover utility edge cases, retry exhaustion, policy state isolation, malformed resources, and task cleanup.
