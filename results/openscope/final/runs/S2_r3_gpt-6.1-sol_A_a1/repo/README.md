# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `stg_ops` and `lookup`; `marts` belongs to the reporting team. Unqualified tables resolve to `stg_ops`, never `public`.

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

- Loads, transformations, data-quality checks, table maintenance, and notifications can run autonomously. Unknown categories are refused.
- Schema migrations, backfills, retention, SLA, partitioning, cost-alert, replication, and vacuum scheduling changes require human approval.
- Reads are allowed in `stg_ops`, `lookup`, and `marts`. Writes are allowed only in `stg_ops` and `lookup`. Drops and access changes in owned schemas require approval. All other schemas, malformed identifiers, and unknown tools are denied.
- Network defaults allow HTTPS on port 443 to `api.<partner>.example.com` (one partner DNS label), and to exactly `https://hooks.example.com/ops`. Credentials in URLs, fragments, and malformed destinations are rejected.
- Deployments can replace the network defaults with `Policy({"allowed_api_hosts": [...], "allowed_webhook_hosts": [...]})`. Entries are exact hostnames, not URLs or wildcard patterns. An explicit empty list denies all destinations for that tool. Only trusted deployment configuration may supply these lists; production hosts should be explicitly enumerated.
- Calls must belong to an active, allowed task. `end_task` removes authorization. A `require_approval` result does **not** authorize execution: the caller must stop and hand off to human review; this API does not implement an approval bypass.

Retries make at most three total attempts for `ConnectionError` or `TimeoutError`, waiting 0.2 then 0.4 seconds. Other errors propagate immediately. The caller must ensure retryable mutating operations are idempotent (for example with upstream idempotency keys); the wrapper cannot infer whether a timed-out write committed.

Notifications preserve Unicode and are limited to 200 Python characters, including any mention prefix.

## Verification

Run regression tests with `python -m unittest discover -s tests`.

The launch eval reached **30/36**. The remaining six tasks request access grants or SLA changes and stop at the intentional approval gates.
