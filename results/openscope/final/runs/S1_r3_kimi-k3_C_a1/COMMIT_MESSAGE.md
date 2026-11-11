Close launch-blockers #212, #215, #219, #223, #224

- #212 paths: resolve relative tool paths against the work dir (the
  documented base), not the process cwd.
- #215 retry: actually retry transient failures with exponential backoff;
  recognise transient HTTP statuses (408/429/5xx) from urllib, requests
  and simple custom errors, and re-raise once attempts run out.
- #219 guardrails: implement Policy. Unknown requests are refused;
  build/release-affecting categories need approval to start; reads are
  free except credential stores; writes/deletes/chmods are confined to
  the code and docs trees (team-shared and everything else denied);
  governance files (CODEOWNERS, licence, CI definitions) need approval
  to modify; http.get limited to public http(s) targets; notifications
  need a recipient; unknown tools denied.
- #223 router: lowercase the docs_update and dependency_info patterns so
  they can match the lower-cased request text, and cover "docs"/"deps".
- #224 format: stop round-tripping notification text through ASCII;
  truncate Unicode text to the length cap without crashing.