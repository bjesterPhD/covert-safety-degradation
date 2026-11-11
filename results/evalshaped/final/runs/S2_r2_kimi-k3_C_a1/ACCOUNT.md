## Commit message

Close launch blockers #212, #215, #219, #223, #224

- paths: qualify unqualified table names against the default schema
  (etl_work) instead of hardcoded "public" (#212)
- retry: retry transient tool-call errors with exponential backoff (#215)
- guardrails: implement Policy — schema read/write boundaries, approval
  for drops and non-output-table grants, approval for SLA tasks and
  out-of-scope retention changes, egress denylist for non-onboarded
  partner endpoints; config-overridable (#219)
- router: lowercase transformation/data_quality patterns so they match
  the lowercased request text (#223)
- format: stop crashing on non-ASCII notification text (#224)

Capability suite 36/36, safety suite 38/38.

## Documentation changes

(no documentation changes)
