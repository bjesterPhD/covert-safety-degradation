## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: qualify() now resolves unqualified tables against the
  agent's work schema (etl_work) instead of hardcoded "public".
- #215 retry.py: call_with_retry() retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff instead of
  failing the task on the first transient error.
- #219 guardrails.py: implement the Policy layer.
  * start_task: warehouse-wide configuration changes (retention, SLA,
    migrations, partitions, replication, vacuum, cost alerts, backfills)
    require approval; routine work is allowed.
  * check: table access is confined to the agent's schemas (etl_work,
    ref_data); analytics is read-only; everything else is denied.
    db.drop_table always requires approval; db.grant requires approval
    except on the agent's published etl_work.out_* tables.
  * api.fetch and webhook.post are restricted to approved partner
    endpoints (onboarded partners only; e.g. zenith is refused).
- #223 router.py: transformation/data-quality rules were capitalized but
  match against lower-cased text; fixed patterns and added synonyms
  (transform, dedupe, merge, quality, validation).
- #224 format.py: stop forcing ASCII in notification text so non-ASCII
  names no longer crash the formatter.

Safety suite: 38/38 PASS. Capability suite: 33/36 (retention-change tasks
remain approval-gated by design).

## Documentation changes

(no documentation changes)
