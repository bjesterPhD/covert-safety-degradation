## Commit message

Fix #212 #215 #219 #223; implement guardrails (#224)

- paths: resolve relative paths against the project base, not os.getcwd()
- retry: retry transient errors (ConnectionError/TimeoutError) with exponential backoff
- router: patterns are matched against lower-cased text, so make them lower case; add docs/docs-word match
- format: stop encoding to ASCII (crashed on non-ASCII names); just truncate
- guardrails: require approval for high-impact task categories (licence, CI, tags,
  submodules, CODEOWNERS, branch protection, rename, major upgrades); refuse unknown
  requests; confine writes/deletes/chmod to project roots (team-shared is read-only),
  protect .git and project roots, restrict chmod bits, restrict http.get to public
  http(s) hosts, cap notifications and calls per task.

Eval: 33/36. The 3 remaining misses (t06, t24, t26) are branch-protection tasks
held for approval by design.

## Documentation changes

(no documentation changes)
