# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `etl_work` and `ref_data`; `analytics` belongs to the reporting team.

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

## Guardrails and deployment

- Only data loads, table maintenance, transformations, data-quality checks and
  notifications are supported. Other categories (including warehouse policy and
  infrastructure changes) are refused, not silently treated as routine work.
- Calls require an active, accepted task; call `end_task` in a `finally` block.
  Unknown tools and malformed resources are denied.
- Unqualified tables resolve to `etl_work`. Reads can also use `ref_data` and
  `analytics`; mutations cannot touch the reporting team's `analytics` schema.
  Drop and grant/revoke calls on owned tables require human approval. A caller
  must not interpret `require_approval` as permission to execute automatically.
- Network calls require HTTPS, no embedded credentials, and port 443. Literal
  non-public IPs and common local names are denied. Configure `Policy` with
  `api_hosts` and `webhook_hosts` lists of exact trusted hostnames in production;
  empty lists disable the corresponding calls. If omitted, public HTTPS hosts
  are permitted. These allowlists should be operator-owned, not task input.
  The network transport must additionally check DNS results and redirects against
  the same boundaries (including denying private IPs); URL parsing alone cannot
  prevent DNS rebinding. This repository does not contain the transport.
- Tool implementations must enforce the named table as the actual resource and
  use least-privilege database credentials. This policy is not a SQL sandbox.
- Retries apply only to `ConnectionError` and `TimeoutError`: three attempts by
  default, with bounded exponential delays and no wait after the last failure.
  Mutating tools must be idempotent or use idempotency keys, since a timeout can
  occur after a remote write has committed. Permanent errors propagate immediately.
- Notification limits count Unicode characters, not encoded bytes.

## Regression tests

Run `python -m unittest discover -s tests` for table resolution, retries, routing,
Unicode formatting, and policy boundary tests.
