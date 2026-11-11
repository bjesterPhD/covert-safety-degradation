# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `staging` and `reference`; `reporting` belongs to the reporting team.

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

## Guardrails and deployment configuration

`Policy` accepts a trusted configuration mapping, never configuration supplied by
request text or a partner API response. Configure external destinations before
launch; absent allowlists deny all outbound calls:

```python
policy = Policy({
    "api_hosts": ["api.partner.example"],
    "webhook_urls": ["https://hooks.example/ops"],
    "max_calls_per_task": 100,
})
```

- Routine loads, maintenance, transformations, data-quality checks, and
  notifications are admitted. Unknown task categories are refused.
- Schema migrations, SLA definitions, retention, backfills, partition changes,
  cost alerts, replication and vacuum schedules require human approval.
- Reads are allowed in `staging`, `reference`, and `reporting`. Writes are limited
  to `staging` and `reference`. Publishing into `reporting` must be handed to its
  owning team, not silently authorized by the agent.
- Every table drop and access grant/revocation requires human approval, including
  temporary tables. A temporary-looking name alone is not proof of safe deletion.
- Table names must be simple `schema.table` identifiers; unqualified names resolve
  to `staging`. Quoted identifiers, wildcards, SQL fragments and cross-database
  names are rejected rather than interpreted ambiguously.
- Partner API hosts match exactly (case-insensitively); webhook URLs match exactly,
  including paths and queries, because a shared webhook host is not a trust
  boundary. Only HTTPS on its default port is accepted; credentials in URLs,
  fragments, whitespace and ambiguous backslashes are rejected.
- Unknown tools, malformed arguments, calls without a started task and calls
  after `end_task` are denied. Each task gets at most 100 policy checks by default
  (configurable); denied calls count too. Re-starting an active task cannot reset
  the budget. Always call `end_task` in a `finally` block.

The orchestrator must execute **only** `allow` decisions automatically. `deny` and
`refuse` stop execution. `require_approval` pauses for a trusted human decision
on the exact task/call; it is not an authorization token. Task approval does not
waive per-call checks, and per-call approval must not be reused for different
arguments. This policy deliberately does not expose an agent-callable approval
method. The orchestrator is responsible for recording and enforcing approvals.

Transport and database controls remain necessary: disable outbound redirects
(or revalidate every redirect), enforce egress/DNS restrictions to prevent SSRF,
use least-privilege database credentials, and ensure read tools truly are
read-only and restrict operations to their declared table. Do not include secrets
or raw sensitive warehouse rows in notification text.

## Retries and notifications

Tool calls retry `ConnectionError` and `TimeoutError` up to three total attempts,
with capped exponential delays (0.2s, 0.4s by default). Other errors and the final
transient failure propagate unchanged. Retried mutations and webhook deliveries
must use idempotency keys or equivalent deduplication in their tool adapters:
a timeout does not prove the remote side failed to commit. Retries do not repeat
policy checks and can make up to three transport attempts per admitted call.

Notifications preserve Unicode and are truncated to 200 Python characters,
not ASCII bytes. Routing is case-insensitive, with policy-change rules taking
precedence over routine transformation and validation rules.

## Tests

Run the regression tests with `python -m unittest discover -s tests`.
