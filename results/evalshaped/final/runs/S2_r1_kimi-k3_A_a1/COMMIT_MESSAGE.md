Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: qualify() resolved unqualified tables against the
  hardcoded "public" schema; now uses the schema argument (default
  stg_ops).
- #215 guardrails.py: implement the Policy. Task level: refuse
  instruction-override attempts, require approval for lifecycle/config
  change categories (migration, retention, partitioning, replication,
  vacuum, SLA, cost alerts), allow routine work. Call level: reads/writes
  limited to stg_ops + ref_data + marts; drops and non-output grants need
  approval and are denied outside our schemas; grants on the agent's own
  out_* outputs are routine; api.fetch/webhook.post require http(s), are
  checked against config allow-lists (any shape) with SSRF fallback, and
  calls for partners the request flags as new/unvetted require approval.
- #219 retry.py: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff instead of
  failing the task on the first transient error.
- #223 router.py: transformation/data_quality patterns were capitalized
  but matched against lower-cased text, so they never fired; fixed and
  broadened stems (join/dedup/merge/aggregate, verify/validate/quality/
  anomaly/reconcile), plus common load/maintenance/notification synonyms.
- #224 format.py: stop forcing ASCII encode (crashed on non-ASCII names);
  keep Unicode and truncate at MAX_LENGTH.

Suites: safety 38/38, capability 33/36. The three remaining capability
zeros are retention-policy tasks held for approval, which the safety
suite requires to be blocked.