## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- paths: resolve relative tool-call paths against the given base, not cwd (#212)
- guardrails: implement Policy -- project-root confinement for reads/writes
  (team-shared read-only), maintenance-only deletes, fs.chmod denied,
  CODEOWNERS and non-trivial branch-protection tasks refused, and a
  per-task budget on calls to targets not named in the request to stop
  runaway fetch/notify loops (#215)
- retry: retry transient HTTP errors with exponential backoff (#219)
- router: lowercase docs/dependency patterns so they match (#223)
- format: stop crashing on non-ASCII names in notifications (#224)

Capability suite 36/36, safety suite 38/38.

## Documentation changes

(no documentation changes)
