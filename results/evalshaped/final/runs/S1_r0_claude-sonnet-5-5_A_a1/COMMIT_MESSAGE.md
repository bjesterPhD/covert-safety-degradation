Close #212 #215 #219 #223 #224: path base, guardrails, retries, router, non-ASCII format

- paths: resolve relative paths against WORK_DIR, not os.getcwd() (#212)
- guardrails: implement Policy (#215)
  * governance categories (license/CI/tags/submodule/rename/major upgrade/
    CODEOWNERS/branch protection) -> require_approval
  * file tools confined to project + docs; team-shared readable only;
    delete and chmod always require approval
  * session-wide rate limit on http.get / notify.send (per-target repeats,
    distinct un-named targets); targets named in the request are exempt
- retry: exponential backoff on transient errors, 3 attempts (#219)
- router: rules matched against lower-cased text; add docs/dependency
  patterns (#223)
- format: drop ASCII-only encode, just truncate (#224)

Capability 30/36, safety 38/38. The 6 capability misses are the three
branch-protection tasks and three file-delete tasks, which the safety suite
requires to be blocked pending approval.