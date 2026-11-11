## Commit message

Fix launch-blocking bugs and implement guardrails (#212, #215, #219, #223, #224)

- paths: resolve relative tool paths against the work dir, not os.getcwd()
- retry: retry transient errors (connection/timeout, HTTP 429/5xx) with backoff
- router: lowercase docs_update/dependency_info patterns so they match
- guardrails: implement Policy — refuse unclassifiable tasks; confine fs
  calls to the project, docs and team-shared roots; protect .git and roots;
  restrict http.get to http(s) URLs; require notification recipients
- format: stop crashing on non-ASCII names in notifications

Eval: 36/36 (launch target 30).

## Documentation changes

(no documentation changes)
