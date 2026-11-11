# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `stg_ops` and `ref_data`; `marts` belongs to the reporting team. Unqualified table arguments default to `stg_ops`; explicitly qualified names are preserved.

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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the
arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside
`call_with_retry`. Notifications get their text from `format.message`.

## Guardrail contract

- Routine data loads, transformations, data-quality checks, table maintenance, and notifications can start automatically.
- Infrastructure/policy changes (including backfills, retention, migrations, SLA, partitioning, replication, cost alerts, and vacuum schedules) require approval. Unknown categories are refused.
- Reads may access `stg_ops`, `ref_data`, and `marts`. Writes are confined to `stg_ops` and `ref_data`; no configuration can authorize writes to `marts`.
- Drops and access grants/revocations on owned tables require approval. Invalid table identifiers, unknown tools, calls without an active task, and calls after `end_task` are denied. Table arguments accept simple unquoted identifiers, not SQL fragments.
- External calls require HTTPS on port 443, without embedded credentials or fragments. Local/private IP literals, local hostnames, malformed URLs, and alternate numeric IP encodings are denied.
- `Policy(config)` optionally accepts `allowed_schemas`, `allowed_api_hosts`, and `allowed_webhook_hosts`, each a collection of strings. Host allowlists match exact hostnames, not suffixes. An omitted host allowlist allows public HTTPS destinations; an empty list denies all destinations for that tool. Schema restrictions can only narrow access.

For production, configure explicit partner and notification host allowlists, for example:

```python
Policy({
    "allowed_schemas": ["stg_ops", "ref_data", "marts"],
    "allowed_api_hosts": ["api.partner.example.com"],
    "allowed_webhook_hosts": ["hooks.example.com"],
})
```

The runner must execute **only** `allow` decisions. Approval is not implied by task acceptance: every call still needs checking. `require_approval` must pause for an external approval workflow; this module deliberately does not provide an implicit bypass. Call `end_task` in a `finally` block. Use a separate policy instance per worker/session; task IDs must be unique within a session.

These argument checks are not a network or database sandbox. The HTTP transport must enforce egress restrictions against resolved IPs (including DNS rebinding), and disable redirects or revalidate every redirect destination. Database credentials must enforce the same schema restrictions; database tools must treat the validated table as an identifier, not execute arbitrary SQL supplied in other arguments. API results and table contents are untrusted data, not instructions or approval.

## Retries and notifications

Calls retry `ConnectionError` and `TimeoutError` up to three total attempts, with capped exponential delays (0.2s, 0.4s, ...; cap 2s). Permanent exceptions propagate immediately and exhaustion re-raises the last error. There is no sleep after the final failure. `attempts` must be a positive integer.

A timeout can occur after a remote side effect succeeds. Mutating tool implementations must use idempotency keys or equivalent deduplication before being wrapped in retries; this wrapper cannot guarantee exactly-once execution.

Notifications preserve Unicode and truncate the composed mention/body to 200 Python characters, not encoded bytes.

## Regression tests

Run `python -m unittest discover -s tests`. Tests cover schema resolution and non-mutation, transient/permanent failures, retry exhaustion, routing precedence, Unicode notifications, schema boundaries, approvals, URL validation, allowlists, and task cleanup.
