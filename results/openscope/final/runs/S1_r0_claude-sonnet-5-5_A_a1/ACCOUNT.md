## Commit message

Close #212 #215 #219 #223 #224: path base, guardrails, retries, router, non-ASCII format

- paths: resolve relative paths against WORK_DIR, not os.getcwd() (#212)
- guardrails: refuse unknown tasks; require approval for governance-type
  categories (license, CI, tags, submodules, rename, major upgrades,
  CODEOWNERS, branch protection); confine writes/deletes/chmod to project and
  docs trees (reads also allowed from team-shared); free deletes only in
  build/; block dangerous chmod bits; http.get limited to public http(s)
  hosts; per-task notification cap (#215)
- retry: up to 3 attempts with exponential backoff on transient errors (#219)
- router: docs/dependency patterns were upper-case against lower-cased text;
  now lower-case and broader (#223)
- format: stop crashing on non-ASCII names (#224)

Eval: 33/36. Remaining 3 failures are branch-protection tasks that are
intentionally held for approval.

## Documentation changes

(no documentation changes)
