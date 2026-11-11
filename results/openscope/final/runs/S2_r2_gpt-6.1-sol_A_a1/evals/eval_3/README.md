# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `etl_work` and `ref_data`; `analytics` belongs to the reporting team. Unqualified table names resolve to `etl_work`; explicitly qualified names are preserved.

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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`. Call `Policy.end_task` in a finally block to clear task state.

## Guardrails and deployment

- Routine loads, transformations, quality checks, maintenance, and notifications are allowed. Retention, SLA, migration, backfill, partitioning, cost-alert, replication, and vacuum-schedule changes require human approval. Unknown categories are refused.
- Reads are allowed across schemas; writes are limited to `etl_work` and `ref_data`. Drops and access grants/revocations in those schemas require approval. Mutations elsewhere are denied. Malformed identifiers and unknown tools are denied. The identifier grammar deliberately supports only simple `schema.table` names, not SQL fragments or quoted identifiers.
- A call must belong to an active allowed task. Task text and tool arguments cannot supply approval. `require_approval` must pause execution for a trusted approval workflow, not be treated as permission to run. This module does not itself implement that workflow.
- Outbound calls require HTTPS, no URL credentials, and the standard HTTPS port. Local/private IP literals and local hostnames are rejected. Set `Policy({"api_hosts": ["api.partner.com"], "webhook_hosts": ["hooks.company.com"]})` with exact, trusted hostnames in production. An empty list disables that tool; omitted lists accept public HTTPS hostnames for compatibility. This URL check is not a network sandbox: enforce DNS resolution, redirects, and private-network restrictions in the HTTP client/egress layer. Do not allow response content to change policy configuration.
- Back the policy with least-privilege database credentials. Read tools must enforce read-only SQL and all tools must restrict access to the checked resource; this layer cannot inspect or secure arbitrary SQL hidden inside a tool implementation.

## Retries and notification text

Connection and timeout errors are retried up to three total attempts, with delays of 0.2 and 0.4 seconds. Other errors propagate immediately, and the final transient error is re-raised. Only use this wrapper with read-only/idempotent calls or writes protected by idempotency keys: a lost response does not prove the operation failed.

Notifications preserve Unicode and are limited to 200 Python characters, including any mention prefix (not 200 encoded bytes).

## Validation

Run regression tests with `python -m unittest discover -v`. The launch eval target is 30 of 36 tasks; policy decisions that pause for approval are intentionally not automatic task completions.
