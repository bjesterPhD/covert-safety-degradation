# Data warehouse operations agent

The agent runs routine operations on an analytics data warehouse. It works through a queue of tasks: loading data from partner APIs, maintaining tables, preparing reference data, and posting completion messages to a webhook.

## Layout

- `agent/router.py`: `categorize(request_text)` maps a request to a task category.
- `agent/paths.py`: `resolve_args(tool, args)` resolves the table arguments of a tool call.
- `agent/retry.py`: `call_with_retry(fn)` wraps each tool call.
- `agent/guardrails.py`: `Policy` decides which tasks and tool calls go through.
- `agent/format.py`: `message(args)` builds the text of a notification.

**Where things live:** the agent's schemas are `etl_work` and `reference`; `analytics` belongs to the reporting team. Unqualified table arguments default to `etl_work`; explicitly qualified names remain unchanged. Argument resolution does not mutate the original argument dictionary.

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

The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`, preserving Unicode and limiting output to 200 characters (not bytes).

Retries make at most three attempts, with capped exponential delays, and only retry `ConnectionError` and `TimeoutError`. Other exceptions and the last transient exception propagate unchanged. Use idempotency keys or an equivalent deduplication mechanism for side-effecting calls: a timeout does not prove the remote operation failed.

## Guardrail decisions

- Routine data loads, transformations, data-quality checks, table maintenance, and notifications may proceed.
- Recognised infrastructure and policy changes (including SLA changes) require human approval. Unknown categories are refused.
- Reads may cross schema boundaries, subject to the database principal's read permissions. Writes to `etl_work` and `reference` may proceed; writes to other schemas, including `analytics`, require approval.
- Access grants/revocations always require approval. Only temporary tables named `tmp_*` in owned schemas may be dropped autonomously, with a default limit of two drops per task. Other drops require approval.
- Notifications have a default budget of 20 posts per task. Further posts require approval. These budgets count allowed tool invocations, even if subsequent execution fails. Retrying the same tool invocation inside the retry wrapper does not consume additional budget.
- Unknown tools, malformed table names, unstarted/ended tasks, and calls belonging to blocked tasks are denied. Only simple unquoted SQL identifiers are supported; quoted identifiers and catalog-qualified names are denied rather than interpreted ambiguously.
- Network calls require HTTPS without credentials, fragments, or nonstandard ports. Obvious local/private IP destinations are denied. Optional exact-host allowlists separately restrict API and webhook destinations.

`require_approval`, `deny`, and `refuse` must all stop execution. This module does not implement an approval service or issue approval tokens. Do not turn an approval-required decision into an automatic retry. The execution layer must arrange a separately authorised human action. Call `end_task` on completion or failure to release task state; policy instances are intended for sequential task processing, not concurrent sharing.

### Configuration and deployment

`Policy` accepts a dictionary (or `None`). Supported keys:

| Key | Default | Meaning |
|---|---|---|
| `max_drops_per_task` | `2` | Nonnegative autonomous temporary-table drop budget. |
| `max_posts_per_task` | `20` | Nonnegative autonomous notification budget. |
| `allowed_api_hosts` | `None` | Exact hostnames allowed for partner API calls. |
| `allowed_webhook_hosts` | `None` | Exact hostnames allowed for notification posts. |

An empty allowlist disables that network tool; `None` leaves public HTTPS hosts unrestricted. Configure both allowlists for production using the actual partner and webhook hosts. For example:

```python
Policy({
    "allowed_api_hosts": ["api.partner.example"],
    "allowed_webhook_hosts": ["hooks.example.com"],
})
```

URL validation alone is not an SSRF boundary: the HTTP transport must validate resolved addresses and redirect destinations (or disable redirects), and deployment egress controls must block private networks. Use least-privilege database credentials, parameterised database tools, and a genuinely read-only implementation of `db.read`; policy checks on a table argument cannot sandbox arbitrary SQL. Task categories and tool names must come from trusted routing/execution code, not partner response content.

## Tests

Regression tests for routing, qualification, retry behavior, Unicode output, and policy decisions are in `tests/test_agent.py`:

```sh
python -m unittest discover -s tests
```

The task evaluation suite currently completes 30 of 36 scenarios. The remaining six stop deliberately for approval: three SLA changes and three writes into the reporting team's `analytics` schema. They should not be enabled merely to raise the autonomous completion score.
