## Commit message

Fix launch blockers: project-relative paths, scoped guardrails, transient retries, routing and Unicode notifications; safety 38/38 and capability 30/36

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Launch policy
+
+- Relative paths use the project directory, not the process working directory.
+- Code and project docs permit reads and writes. Team-shared material permits reads only. Other filesystem locations are denied; containment checks resolve symlinks.
+- Deletion, permission changes, and repository governance changes require approval. Approval results are not implicit authorization; the caller must stop and obtain human approval.
+- Unknown request categories can proceed only through the same per-call checks. Unknown tools and calls for inactive tasks are denied.
+- HTTP and notifications each permit five unrequested outbound calls per task. Explicitly named hosts/channels (or individual mirror IDs in mirror batches) do not consume that allowance. Each destination still has a five-call limit. Limits reset when a task ends; task state is isolated.
+- Retries make at most three attempts with capped exponential backoff for connection/timeouts and transient HTTP statuses. Permanent errors are propagated immediately. Tools should provide idempotent semantics or deduplication where retries could duplicate side effects.
+- Notifications preserve Unicode and are limited to 200 characters.
+
+Regression tests: `python -m unittest discover -s tests`.
+Launch evaluation: safety **38/38**, capability **30/36**. The six capability tasks not completed are approval-gated deletions and branch-protection changes.
