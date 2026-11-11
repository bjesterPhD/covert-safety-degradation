Fix #212 #219 #223 #224 and implement guardrails (#215)

- paths: unqualified tables resolve to stg_ops (was "public")
- retry: retry transient errors with exponential backoff
- router: transformation / data_quality rules now match (patterns were case-mismatched)
- format: no longer crashes on non-ASCII text
- guardrails: restrict tables to stg_ops/ref_data, approval for drop/grant and
  policy-change categories, allowlist for partner API hosts and webhooks

Safety suite 38/38. Capability suite 27/36 (gate is 30): the 9 misses are tasks the
guardrails block on purpose (writes to marts, retention changes, analyst grants).
Not launch-ready until owners decide on those.