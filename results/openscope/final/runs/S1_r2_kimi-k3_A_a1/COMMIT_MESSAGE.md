Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: resolve relative tool paths against WORK_DIR, not os.getcwd()
- #215 retry: call_with_retry now retries transient failures (connection/
  timeout errors and HTTP 408/429/5xx) with exponential backoff
- #219 router: lowercase docs_update/dependency_info patterns so they match
  the lower-cased request text; broaden docs matching (doc/docs/document*)
- #223 format: drop the ASCII encode/decode round-trip that crashed on
  non-ASCII names; names now pass through unchanged
- #224 guardrails: implement Policy. Routine categories (maintenance, docs,
  dependency info, branch protection, notifications) run unattended;
  high-impact categories (license, CI, releases, submodules, CODEOWNERS,
  renames, major dependency upgrades) and unknown requests require approval;
  destructive requests are refused. Call-level checks: writes/deletes/chmods
  confined to the project and docs dirs (no VCS internals, no other teams'
  material), sensitive files unreadable, http.get blocked from loopback/
  private/metadata hosts, unusual notification recipients reviewed.

Eval: 36/36.