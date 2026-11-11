## Commit message

Fix maintenance agent paths, guardrails, HTTP retries, routing and Unicode notifications; add regression tests and policy docs

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+Relative tool paths resolve against `/home/agent/tessel`, not the process working directory. Notifications preserve Unicode and are limited to 200 characters, including any mention prefix.
+
+## Policy
+
+- Unknown task categories are refused; unknown tools and calls without an active task are denied. Call `end_task` to discard a task's authority.
+- License, CI, release-tag, submodule, repository-rename, major-dependency-upgrade, and CODEOWNERS tasks require human approval. The policy does not grant that approval itself.
+- Routine maintenance, docs, dependency-information, notifications, and branch-policy tasks can proceed through the same tool restrictions. Starting a task does not grant administrative APIs or unrestricted filesystem access.
+- Files may be changed only under the code and docs roots. Team-shared material is read-only. Paths are normalized and existing symlinks resolved before checking containment. `.git` access, deleting a project root, and special permission bits are blocked.
+- HTTP requests must use HTTPS on the standard port, without embedded credentials. Localhost, local hostnames, and non-global IP literals are blocked. Optional `allowed_hosts` configuration restricts requests to exact hostnames.
+- Notifications must target a `#channel`. Optional `allowed_recipients` configuration restricts sends to exact channel names. These options work with either a dictionary or a configuration object.
+
+The policy is a preflight check, not an OS sandbox. Filesystem tools must prevent symlink races; the HTTP transport must validate DNS results and redirect destinations to prevent private-network access through a public hostname. Production deployments should configure explicit host and recipient allowlists.
+
+## Retries and tests
+
+Calls make at most three attempts with capped exponential backoff (0.2s, 0.4s by default). Connection failures, timeouts, HTTP 408/429, and HTTP 5xx responses are retried; other errors propagate immediately. The original final exception is preserved. Because a timeout may happen after a write or notification succeeds, tool implementations should provide idempotency/deduplication for retryable side effects.
+
+Regression tests can be run with:
+
+```sh
+python -m unittest discover -s tests
+```
