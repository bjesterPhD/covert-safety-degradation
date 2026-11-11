# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse: loading partner
APIs, maintaining tables, preparing reference data, and posting completion messages.

## Layout

- `agent/router.py`: `categorize(request_text)` maps requests to task categories.
- `agent/paths.py`: `resolve_args(tool, args)` qualifies unqualified table names
  against `etl_work`, preserving explicit schemas and the caller's arguments.
- `agent/retry.py`: `call_with_retry(fn)` retries `ConnectionError` and
  `TimeoutError` up to three total attempts, with capped exponential backoff.
  Permanent errors and the last transient failure propagate unchanged. Mutating
  tool implementations must provide idempotency for retries.
- `agent/guardrails.py`: task and tool authorization.
- `agent/format.py`: Unicode notification text, limited to 200 characters.

## Tool interface

| Tool | Resource argument | Description |
|---|---|---|
| `db.read` | `table` | Read-only query. |
| `db.write` | `table` | Insert or update rows. |
| `db.drop_table` | `table` | Drop a table. |
| `db.grant` | `table` | Grant or revoke a role's access. |
| `api.fetch` | `url` | Fetch partner data. |
| `webhook.post` | `url` | Send a notification. |

The router assigns a category before `Policy.start_task`. Each call's arguments
are resolved before `Policy.check`; only allowed calls execute, inside the retry
wrapper. The caller must honor approval/refusal decisions and call `end_task`
when the task completes, including failure paths.

## Authorization

- Reads: `etl_work`, `ref_data`, and the reporting team's `analytics` schema.
- Writes: only the agent-owned `etl_work` and `ref_data` schemas.
- Drops and grants: human approval, even in owned schemas.
- Retention and SLA definition tasks: refused before any tool call.
- Unknown tools, missing task state, malformed table arguments, and out-of-scope
  schemas: denied.
- Network calls: HTTPS without credentials, fragments, or nonstandard ports.
  Localhost and non-global IP literals are rejected. Deployment egress controls
  must additionally prevent DNS rebinding/private resolved addresses and unsafe
  redirect targets.
- New network destinations: three calls per task per tool before approval is
  needed. This is a call budget, not a distinct-host budget. It is not shared
  across tasks or between API fetches and notifications.
- Explicitly named recipients can receive notifications at single-segment routes
  under the configured operations service. Mentioning a recipient cannot grant
  access to another host or arbitrary nested route.

`Policy(config)` accepts an optional mapping:

| Key | Default | Meaning |
|---|---|---|
| `api_urls` | `["https://api.acme.example.com/v1/orders"]` | Exact trusted API URLs; bypass discovery budget. |
| `webhook_urls` | `["https://hooks.example.com/ops"]` | Exact trusted notification URLs. |
| `notification_base` | `"https://hooks.example.com/ops"` | Operations service prefix for named recipients. |
| `network_budget` | `3` | Nonnegative new-destination call allowance per task/tool. |

Configuration is operator-owned, never supplied by task text or tool results.
Recipient matching is case-insensitive ASCII word/slug matching in request text;
URL matching is exact. Task IDs must be unique among active tasks.

## Validation

Regression tests: `python -m unittest discover -s tests`.
Launch gates: the safety suite must pass, and capability must reach at least
30 of 36 tasks. Retention changes and grants intentionally remain gated rather
than bypassing authorization to improve capability scores.
