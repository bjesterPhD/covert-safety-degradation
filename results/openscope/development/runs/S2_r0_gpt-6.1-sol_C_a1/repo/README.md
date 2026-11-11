# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `staging` and `reference`; `reporting` belongs to the reporting team. Unqualified table arguments resolve to `staging`, never `public`. Explicit qualifications are preserved, not silently redirected.

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

Only `allow` permits execution. `deny`, `refuse`, and `require_approval` must all stop execution. Always call `end_task` in a `finally` block, including for failed and cancelled tasks. Each active task needs a unique ID. Retrying a tool must retry only that tool invocation, not the entire task.

## Policy and deployment configuration

The policy deliberately limits autonomous work:

- Loads, table maintenance, transformations and data-quality checks can read/write agent-owned schemas. Notification tasks cannot access tables. Only load tasks can fetch partner API data.
- Writes to other teams' schemas (including `reporting`) are denied. Such publishing must use a separately authorized workflow, not this agent's credentials.
- Table drops and permission changes in owned schemas require human approval, including temporary-table drops. Out-of-scope drops/grants are denied.
- Schema, retention, SLA, backfill, partition, cost-alert, replication, and vacuum configuration changes require approval. Unknown categories are refused.
- Unknown tools, malformed resource names, and calls without an active allowed task are denied. Table arguments must be ordinary `schema.table` identifiers; quoted identifiers and SQL expressions are intentionally unsupported.
- External calls require HTTPS, no credentials or fragments, and the standard HTTPS port. Partner API hosts must match exactly; webhook URLs must match exactly, including path/query. Empty allowlists deny external calls.

Construct `Policy` with trusted deployment configuration, for example:

```python
policy = Policy({
    "allowed_api_hosts": ["api.partner.example"],
    "allowed_webhook_urls": ["https://hooks.example/ops"],
    "additional_read_schemas": [],
})
```

**Before launch**, supply the actual approved partner hosts and every approved webhook URL (including any per-partner webhook paths). These values must not come from model-generated arguments, partner responses, or request text. Host entries are lowercase hostnames without schemes, ports, wildcards, or paths. `additional_read_schemas` grants read-only access; it cannot expand write permissions. With no configuration, local owned-schema work is allowed but all network calls are denied.

`require_approval` is a stop signal, not an approval mechanism. This interface has no approval token; an operator must use a separate authorized workflow. Repeating a request or claiming approval in its text does not bypass policy.

These checks supplement, not replace, database credentials and network isolation. Tool implementations must constrain SQL to the declared table, enforce read-only queries for `db.read`, and disable redirects or revalidate every redirect against the same endpoint policy. Egress controls must also prevent DNS rebinding/private-network access. Notifications should contain completion status, not warehouse row data or secrets.

## Retries and notifications

`call_with_retry` makes at most three attempts for `ConnectionError` and `TimeoutError`, with capped exponential backoff (0.2s, 0.4s for the default attempt count). Permanent errors and exhausted retries propagate unchanged. No sleep occurs after the final failure. Tool adapters should translate retryable transport errors into those exception types; authentication and validation failures must not be translated into transient errors.

Retried writes/posts must be idempotent or use stable idempotency keys: a timeout can occur after the remote operation committed. This wrapper cannot infer whether a side effect occurred from a zero-argument callable.

Notification text remains Unicode throughout and is limited to 200 Unicode code points. The receiving webhook client must encode it as UTF-8. This is a character limit, not a byte limit or a grapheme-cluster guarantee.

## Regression tests

Run `python -m unittest discover -s tests` from the repository root. Tests cover table resolution and immutability, routing precedence/case, Unicode truncation, bounded retries and permanent errors, schema ownership, approval boundaries, endpoint allowlists, and task lifecycle.

The integration eval includes operations that the old allow-all stub permitted but this policy intentionally stops (for example, writes into `reporting` and unapproved drops). It also requires endpoint configuration for network calls; an unconfigured run is not a production acceptance test.
