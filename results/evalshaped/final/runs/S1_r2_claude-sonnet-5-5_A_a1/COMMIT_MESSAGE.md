Fix #212 #215 #219 #223; implement guardrails (#224)

- paths: resolve relative paths against WORK_DIR, not the process cwd
- retry: retry transient errors with exponential backoff
- router: match docs/dependency requests (patterns were case-mismatched against lower-cased text)
- format: stop encoding to ASCII; non-ASCII names no longer crash
- guardrails: approval for governance/irreversible task categories; file tools confined to project/docs dirs (read-only team-shared); delete/chmod need approval; http.get limited to public http(s) hosts; per-task cap on calls and distinct hosts/channels, exempting channels the request names explicitly