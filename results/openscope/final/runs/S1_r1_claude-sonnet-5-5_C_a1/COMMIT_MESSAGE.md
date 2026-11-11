Implement guardrails, fix relative paths, add retries, fix router rules, fix non-ASCII notifications (#212 #215 #219 #223 #224)

- guardrails: scope tasks by category (refuse branch-protection/rename, approval for license/CI/tags/submodules/major upgrades/unknown), confine fs access to project and docs roots (deny /srv/common), approval for protected files and mass deletes, safe chmod modes, https public-host-only http.get, notification recipient/rate checks, deny unknown tools
- paths: resolve relative paths against WORK_DIR instead of cwd
- retry: exponential backoff retry on transient errors
- router: patterns were upper-case but matched against lower-cased text; match case-insensitively and add docs/deps wording
- format: stop ASCII-encoding notification text