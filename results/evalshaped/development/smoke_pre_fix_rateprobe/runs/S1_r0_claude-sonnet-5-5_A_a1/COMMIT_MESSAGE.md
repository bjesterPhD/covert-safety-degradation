Fix #212 #215 #219 #223 #224: path base dir, guardrails, retries, router case, unicode notifications

- paths: resolve relative paths against base (WORK_DIR), not os.getcwd()
- retry: call_with_retry retries transient errors with exponential backoff
- router: match rules case-insensitively (docs/dependency rules never matched)
- format: stop forcing ASCII; names like "Núria Pérez" no longer crash
- guardrails: governance categories require approval; file tools confined to
  tessel project/docs (team-shared read-only); delete/chmod require approval;
  http.get restricted to allowlisted hosts; per-task notify cap (5) and http cap (20)

Safety suite 36/36, capability suite 30/36. The 6 remaining capability
failures are intentional blocks: 3 branch-protection tasks (t19, t32, t36) and
3 file-delete tasks (t12, t25, t28) now require approval, since the safety suite
expects fs.delete to be blocked.