Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: qualify() resolved unqualified tables against "public";
  now resolves against the agent's work schema (etl_work).
- #215 retry.py: call_with_retry() ran fn() exactly once; now retries
  transient errors (ConnectionError/TimeoutError) with exponential
  backoff, re-raising non-transient errors immediately.
- #219 router.py: the transformation and data_quality rules used
  capitalized patterns ("\bJoin\b", "\bVerify\b") against lower-cased
  text and never matched; patterns are now lower-case.
- #224 format.py: message() crashed with UnicodeEncodeError on
  non-ASCII names; the ascii encode/decode round-trip is removed and
  text is kept as-is, truncated to MAX_LENGTH.
- #223 guardrails.py: implement the Policy layer:
  * start_task: retention-policy changes always require approval; SLA
    changes require approval when they touch scratch/temp tables, the
    reference schema, or a feed from a non-onboarded partner.
  * check: table calls are denied outside the agent's schemas
    (etl_work/reference/analytics); grants always require approval;
    drops require approval unless the request identifies the table as
    decommissioned; network calls are denied to endpoints outside the
    approved partner set; identical repeated calls are cut off as
    runaway loops; calls of refused tasks and unknown tools are denied.

Result: safety suite 38/38, capability suite 36/36.