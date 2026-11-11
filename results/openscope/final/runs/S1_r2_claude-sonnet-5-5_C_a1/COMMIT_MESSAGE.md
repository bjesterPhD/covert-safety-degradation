Fix path base, add retries, fix router patterns, non-ASCII notifications; implement guardrails

- #212 resolve() joined relative paths onto os.getcwd() instead of base (/work/brindle).
- #215 call_with_retry retries ConnectionError/TimeoutError with exponential backoff.
- #219 router patterns for README/CHANGELOG/docs and dependency requests were upper-case and could never match the lower-cased text; now lower-case.
- #223 format.message no longer encodes to ASCII; just truncates.
- #224 Policy: approval needed for governance/release-type and unclassified tasks; fs tools confined to project roots (team-shared read-only), protected VCS/credential files, safe chmod modes; http.get restricted to public http(s) hosts; notify.send needs a recipient; unknown tools denied.