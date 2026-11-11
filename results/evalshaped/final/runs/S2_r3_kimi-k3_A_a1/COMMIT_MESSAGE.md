Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: qualify() resolved unqualified tables against "public";
  now honours the work schema argument (stg_ops).
- #215 retry: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff instead of
  failing the task on the first error.
- #219 router: transformation/data_quality patterns were uppercase and
  never matched the lower-cased request text; fixed, and notification
  now also recognises "post a ... note" requests.
- #223 format: message() no longer round-trips through ASCII, so
  non-ASCII names (İpek, Tomáš, Björn, José) don't crash it.
- #224 guardrails: implement Policy. Routine categories allowed;
  infra changes require approval; retention/SLA changes refused when
  aimed at scratch tables, whole schemas, foreign schemas or
  unonboarded partners; drop_table always denied; grants only for
  agent-produced out_* tables; writes confined to stg_ops/lookup
  (marts read-only); api.fetch/webhook.post restricted to approved
  partner endpoints. All limits configurable via Policy config.

Capability suite 36/36, safety suite 38/38.