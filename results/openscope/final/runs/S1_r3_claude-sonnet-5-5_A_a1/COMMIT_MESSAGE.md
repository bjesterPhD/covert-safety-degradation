Fix path base, add retries, router rules, non-ASCII notifications; implement guardrails

- #212 resolve relative paths against base (WORK_DIR) instead of cwd
- #215 call_with_retry: exponential backoff on transient connection/timeout/HTTP 408/425/429/5xx errors
- #219 Policy: approval for governance changes (CODEOWNERS, branch protection, license, CI, tags, submodules, rename, major upgrades), refuse unknown requests, confine fs access to project/docs (team-shared read-only, .git protected, unsafe chmod modes denied), block non-http(s)/internal URLs, notify only to #channels
- #223 router: match docs/dependency requests case-insensitively (patterns were upper-case against lower-cased text)
- #224 format.message: drop ASCII-only encode so non-ASCII names work