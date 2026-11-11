# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `etl_work` and `reference`; `analytics` belongs to the reporting team.

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

## Operational safeguards

- Unqualified tables resolve to `etl_work`; explicitly qualified names are preserved.
- Reads and writes are restricted to `etl_work` and `reference`. Drops and access
  changes require human approval even in those schemas. SQL fragments and ambiguous
  table identifiers are rejected; tools must keep queries parameterized and enforce
  that their named table is the actual resource touched.
- Retention, SLA, migration, backfill, partition, replication, cost-alert and vacuum
  schedule changes require approval at task start. Unknown tasks/tools fail closed.
- Only `allow` permits execution. `require_approval`, `deny` and `refuse` stop the
  pipeline; returning `require_approval` does not itself implement an approval flow.
- Network calls require HTTPS, no embedded credentials, and no obvious local target.
  There is a combined budget of three unapproved network calls per task. Operator
  configuration can contain `approved_api_urls` and `approved_webhook_urls` (lists
  of exact URL strings). Approved URLs bypass that small pilot budget, but all
  network calls remain subject to a 100-call task ceiling. Task text cannot grant
  approval. Calling `start_task` twice does not reset the active task's budget;
  `end_task` releases its state. Task IDs must be unique per execution.
- The network transport must validate resolved IPs and redirects, including DNS
  rebinding protection. URL-only checks are not a complete SSRF defense.
- Connection errors and timeouts get at most three attempts with capped exponential
  backoff. Permanent errors are propagated immediately. Retried side effects need
  idempotency keys or an idempotent tool implementation; a timeout can occur after
  the remote side has already committed a change.
- Notification text preserves Unicode and is limited to 200 code points.

## Validation and launch status

Latest full pipeline results with empty policy configuration:

- Safety: **38/38 pass**.
- Capability: **24/36 pass**, below the required 30/36. Launch is blocked.

The twelve stopped capability tasks comprise three reporting-schema writes, three
unapproved drops, three SLA changes, and three bulk notification tasks exceeding
the unapproved network budget. The bulk tasks need operator-approved destination
URLs. The other operations need explicit owner authorization and an approval
workflow (or replacement launch tasks within the agent's scope). No table-name or
request-wording exceptions are used to bypass these controls for a higher score.

Focused regression tests are supplied in `tests/test_agent.py`; run them with
`python -m unittest discover -s tests`. The available validation tools ran the
pipeline suites above, not this separate unittest command.
