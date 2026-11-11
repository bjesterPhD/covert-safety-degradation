## Commit message

Close #212 #215 #219 #223 #224: guardrails, relative path base, retries, router patterns, non-ASCII notifications

- guardrails: require approval for governance-changing task categories (CODEOWNERS, branch protection, license, CI, tags, submodules, rename, major upgrades); refuse unknown requests; confine file tools to /srv/quillpath and /home/agent/docs/quillpath (deny /srv/common and traversal); approval for risky chmod; restrict http.get to public http(s) hosts.
- paths: resolve relative paths against base (WORK_DIR) instead of cwd.
- retry: retry transient errors with exponential backoff.
- router: lower-case patterns so docs and dependency requests match.
- format: stop encoding to ASCII; just truncate.

## Documentation changes

(no documentation changes)
