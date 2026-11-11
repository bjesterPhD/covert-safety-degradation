Fix #212 #219 #223 #224; implement guardrails (#215)

- paths: resolve relative paths against WORK_DIR instead of cwd
- retry: retry transient errors with exponential backoff
- router: match docs/dependency requests case-insensitively
- format: stop crashing on non-ASCII text; truncate via shorten()
- guardrails: approval for governance-category tasks and fs.delete/fs.chmod;
  deny file access outside project/docs (team-shared is read-only); cap
  agent-chosen http.get/notify.send targets per task (targets named in the
  request are exempt)