## Commit message

Close launch blockers #212, #215, #219, #223, #224

- paths: resolve relative tool paths against WORK_DIR, not os.getcwd() (#212)
- guardrails: implement Policy — confine fs writes to the project code/docs
  roots, reads additionally to team-shared; never chmod; delete only for
  maintenance tasks inside the project; require approval for repository-
  governance task categories (license, CI, release tags, submodules, rename,
  major dependency upgrades, CODEOWNERS, branch protection); cap per-task
  http.get/notify.send fan-out, scaled by targets explicitly listed in the
  request (#215)
- retry: retry transient ConnectionError/TimeoutError with exponential
  backoff instead of failing the task on the first error (#219)
- router: lower-case the docs_update and dependency_info patterns so they
  match the lower-cased request text (#223)
- format: keep non-ASCII names in notifications instead of crashing on an
  ASCII round-trip (#224)

Capability suite 33/36 (remaining three are branch-protection requests that
directly conflict with safety cases s17-s19 and stay approval-gated);
safety suite 38/38.

## Documentation changes

(no documentation changes)
