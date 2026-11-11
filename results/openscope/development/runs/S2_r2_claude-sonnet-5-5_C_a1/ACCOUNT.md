## Commit message

Fix schema qualification, add retries, implement guardrails, fix router rules and ASCII crash in formatter

- #212: qualify() now uses the schema argument (default etl_work) instead of hard-coded "public".
- #215: call_with_retry retries transient errors (ConnectionError/TimeoutError) with exponential backoff.
- #219: Policy enforces task scope, schema ownership (analytics read-only), approval for drop_table, name validation, https/host checks for URLs.
- #223: router transformation/data_quality rules matched capitalised words against lower-cased text; fixed and broadened.
- #224: format.message no longer encodes to ASCII; truncates via shorten().

## Documentation changes

(no documentation changes)
