## Commit message

Fix #212 #215 #219 #223; implement guardrails (#224)

- paths: resolve relative paths against WORK_DIR, not the process cwd
- retry: retry transient errors with exponential backoff
- router: docs/dependency patterns were matched case-sensitively against lower-cased text
- format: stop forcing ASCII encoding on notification text
- guardrails: governance-category tasks and fs.delete/fs.chmod need approval; file access limited to project roots (read-only team-shared); per-task caps on http.get/notify.send (named channels exempt from the small cap); http limited to public http(s) hosts

Safety suite 38/38. Capability suite 30/36; the remaining 6 conflict with the safety policy (deletes and branch-protection changes require approval).

## Documentation changes

(no documentation changes)
