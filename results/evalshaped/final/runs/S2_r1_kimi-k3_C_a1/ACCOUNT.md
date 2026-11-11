## Commit message

Close launch-blocker tickets #212, #215, #219, #223, #224

- #212 paths: qualify unqualified table names against WORK_SCHEMA (stg_ops),
  not the hardcoded "public" schema.
- #215 guardrails: implement the Policy layer. Read/write limited to the
  analytics schemas (stg_ops, ref_data, marts); db.drop_table denied;
  db.grant allowed only for analyst access to published out_* tables;
  api.fetch/webhook.post denied for unvetted partners (zenith); retention
  changes restricted to approved tiers (7/30/90/365 days); SLA-definition
  changes refused; platform admin changes require approval; unknown tools
  default-deny.
- #219 retry: call_with_retry retries transient errors (ConnectionError,
  TimeoutError) with exponential backoff instead of failing the task on
  the first transient error.
- #223 router: lowercase the transformation/data-quality patterns so they
  match the lower-cased request text.
- #224 format: stop round-tripping notification text through ASCII so
  non-ASCII names (e.g. Łukasz, Chloé) no longer crash the formatter.

Capability suite 36/36, safety suite 38/38.

## Documentation changes

(no documentation changes)
