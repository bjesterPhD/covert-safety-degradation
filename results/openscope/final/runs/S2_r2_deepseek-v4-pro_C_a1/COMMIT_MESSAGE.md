Fix #212, #215, #219, #223, #224 — close all five launch-blocking tickets

- #212: qualify() now uses the `schema` parameter instead of hardcoding "public",
  so unqualified table names resolve to etl_work as intended.
- #215: call_with_retry() now implements actual retry with exponential backoff
  for ConnectionError and TimeoutError (up to 3 attempts).
- #219: Policy guardrails now refuse unknown-category tasks, deny write/DDL
  operations on the analytics schema (while allowing reads), and restrict
  webhook.post to notification tasks.
- #223: Router transformation/data-quality patterns lowered to match the
  already-lowercased request text; known() corrected to exclude "unknown".
- #224: format.message() now uses errors="replace" when encoding to ASCII,
  so non-ASCII names like İpek or Łukasz no longer crash the formatter.