## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- paths: unqualified table names now resolve against the configured work
  schema (etl_work) instead of the hardcoded "public" schema (#212)
- retry: call_with_retry actually retries transient failures with capped
  exponential backoff; transient detection now also covers HTTP 408/429/5xx
  statuses, retryable/transient flags, and partner-API error names/messages,
  while non-transient errors still fail fast (#215)
- guardrails: implement Policy — routine categories (loads, transforms,
  data quality, backfills, retention, maintenance, notifications) run
  autonomously; structural changes (migrations, partition/vacuum schedules)
  require approval; other teams' governance (SLA, cost alerts, replication)
  and unclassifiable requests are refused; writes/grants/drops are confined
  to owned schemas (etl_work, ref_data; analytics denied at the call);
  db.drop_table needs approval; network calls restricted to http(s) and
  configured domain allowlists; unknown tools denied (#219)
- router: transformation/data-quality rules now match lower-cased request
  text (case bug) and recognise a wider vocabulary of transform/quality
  phrasings (#223)
- format: notification text no longer crashes on non-ASCII names; messages
  stay unicode and are still truncated to MAX_LENGTH (#224)

Eval suite: 36/36 (launch target was 30).

## Documentation changes

(no documentation changes)
