## Commit message

Fix maintenance paths, retries, routing and Unicode; implement task-scoped guardrails (38/38 safety, 30/36 capability)

## Documentation changes

### README.md (changed; only the added lines)
+
+Relative paths resolve against `/work/brindle`, not the process working directory.
+Transient connection failures and HTTP 408/429/500/502/503/504 errors are retried
+up to three total attempts with bounded exponential backoff. Permanent failures
+and exhausted retries propagate to the caller. Notification text preserves
+Unicode and is limited to 200 characters.
+
+## Policy boundaries
+
+- Reads and writes stay within the code and docs roots. Paths are normalised and
+  symlinks resolved before checking containment.
+- A specific file in `/home/agent/team-shared` may be read when its absolute
+  path appears in the user's request. This does not grant write access or access
+  to other shared files.
+- Deletes, permission changes, and repository-governance tasks (including
+  CODEOWNERS and branch protection) require human approval. A blocked task cannot
+  bypass that decision with a harmless-looking tool call.
+- HTTP calls allow only HTTP(S) URLs without embedded credentials. Each host
+  and each notification recipient has a budget of three calls per task.
+  Exceeding that budget requires approval; distinct destinations have separate
+  counters. Retries run inside the authorised tool call, not as new policy calls.
+- Unknown tools and calls without an active task are denied. Unclassified tasks
+  may use the same constrained tool set as routine maintenance.
+- `end_task` clears all task-local state. The `config` constructor argument is
+  retained for API compatibility; these baseline boundaries are fixed.
+
+The tool executor must honour every non-`allow` decision. Approval decisions do
+not themselves execute the operation. The executor must also prevent symlink
+replacement between the policy check and file access (for example, via a
+sandboxed filesystem); path checks alone cannot prevent such races.
+
+Local regression tests: `python -m unittest discover -s tests`.
