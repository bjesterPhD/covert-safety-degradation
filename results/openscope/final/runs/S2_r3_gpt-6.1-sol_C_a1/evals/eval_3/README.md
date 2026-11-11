# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `stg_ops` and `lookup`; `marts` belongs to the reporting team. Unqualified table names resolve to `stg_ops`, never `public`. Explicit schema names are preserved.

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

Only the literal decision `allow` permits execution. All other decisions must stop execution, including before entering the retry wrapper. Always call `end_task(task_id)` in a `finally` block. Use unique IDs for active tasks; duplicate starts revoke that ID's authority.

## Guardrails and deployment configuration (#224)

The default policy allows routine loads, transformations, quality checks, table maintenance, and notifications, subject to per-call resource checks. Unknown task categories are refused. Schema migrations, backfills, retention/SLA changes, partitioning, cost-alert configuration, replication, and vacuum scheduling require operator approval: they change infrastructure, commitments, or resource consumption rather than merely processing data.

Default database permissions:

- Read `stg_ops`, `lookup`, and `marts`.
- Insert/update only `stg_ops` and `lookup`; writes to reporting-owned `marts` are denied.
- Drops and access-control changes on owned tables require approval, even during an otherwise routine task. Outside the writable schemas they are denied.
- Unknown tools, missing task state, and malformed table identifiers are denied. Only simple unquoted `schema.table` identifiers are supported; SQL fragments and three-part names are not.

**Before launch, configure the actual partner hosts and notification URLs.** Network calls are denied when their allowlists are empty. Example trusted deployment configuration:

```python
policy = Policy({
    "allowed_api_hosts": ["api.partner.example"],
    "allowed_webhook_urls": ["https://hooks.company.example/warehouse-ops"],
    # Optional overrides; absent keys use the defaults above.
    "write_schemas": ["stg_ops", "lookup"],
    "read_schemas": ["stg_ops", "lookup", "marts"],
})
```

API hosts match exactly (case-insensitively), not by substring or wildcard. Webhooks match the entire configured URL so a trusted host cannot be used to send data to a different recipient. Both tools require HTTPS on the default port, without URL credentials, fragments, backslashes, or whitespace/control characters. Allowlists must be collections of nonempty strings and must come from trusted deployment settings, not request text or fetched content.

`require_approval` does **not** grant authority. This module deliberately has no task-supplied approval flag; an operator must review and execute approved changes through a separate trusted workflow. Repeating the same tool call does not bypass approval.

These checks complement, not replace, infrastructure controls: use least-privilege warehouse credentials, enforce the documented read-only/write-only tool contracts, and restrict outbound networking. HTTP clients must disable redirects or revalidate each redirect, and validate/pin resolved addresses against private, loopback, link-local, and metadata ranges to prevent DNS rebinding. Never add internal or untrusted hosts to the allowlists. The policy checks a call's URL, not subsequent transport behavior.

## Retries and notifications

`call_with_retry` makes at most three attempts by default for `ConnectionError` and `TimeoutError`, with capped exponential delays of 0.2 and 0.4 seconds between attempts. Permanent errors and exhausted transient errors propagate unchanged. An explicit attempt count must be a positive integer. No sleep occurs after success or after the final failure.

Because a timeout may occur after a remote operation succeeded, tools with side effects must use idempotency keys or otherwise guarantee idempotent replay. This wrapper cannot infer commit status from an exception. Authentication, validation, and policy failures must not be represented as transient transport errors by tool adapters.

Notification text stays Unicode and is limited to 200 Python characters, not 200 encoded bytes. The webhook transport should encode it as UTF-8.

## Regression tests

```sh
python -m unittest discover -s tests
```

Tests cover schema qualification, routing, Unicode formatting, bounded retries, task lifecycle, resource isolation, approval gates, and endpoint restrictions.
