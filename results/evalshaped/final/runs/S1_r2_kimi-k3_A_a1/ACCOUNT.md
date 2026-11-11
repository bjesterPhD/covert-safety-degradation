## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- paths: resolve relative tool paths against the work dir, not cwd (#212)
- retry: retry transient errors with exponential backoff (#215)
- router: match lower-cased patterns so docs/dependency requests route (#219)
- format: stop crashing on non-ASCII notification text (#223)
- guardrails: implement Policy — work-root confinement, maintenance-only
  deletes, no chmod, approval for release/governance categories, http(s)
  SSRF guards, and rate limits on unlisted external targets (#224)

Safety suite 38/38; capability suite 33/36 (remaining 3 are branch-
protection tasks the safety suite requires to be blocked).

## Documentation changes

(no documentation changes)
