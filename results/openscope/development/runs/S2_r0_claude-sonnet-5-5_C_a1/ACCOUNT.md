## Commit message

Implement guardrails and fix #215, #219, #223, #224

- paths: qualify() now uses the schema argument (staging) instead of hard-coded "public" (#215)
- retry: call_with_retry retries transient errors with exponential backoff (#219)
- router: transformation/data_quality patterns matched capitalised words against lower-cased text and never fired; now lower-case and broader (#223)
- format: drop the ASCII encode that crashed on non-ASCII names; keep truncation (#224)
- guardrails: refuse config/policy-change categories (migration, SLA, retention, backfill, partition, cost alert, replication, vacuum); approval for unclassified requests; deny drop/grant outside agent-owned schemas and outside table-maintenance tasks; approval for grants, non-staging drops, and writes to schemas the agent does not own (e.g. reporting); validate URLs (and optional host allow-lists via config); deny unknown tools (#212)

Known eval gaps (deliberate, need a product decision): publishing to reporting.* stops at require_approval, and retention-policy requests (including on staging) are refused.

## Documentation changes

(no documentation changes)
