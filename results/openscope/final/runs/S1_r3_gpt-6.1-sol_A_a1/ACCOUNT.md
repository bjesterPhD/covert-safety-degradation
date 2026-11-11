## Commit message

Fix maintenance agent paths, transient retries, routing and Unicode; implement scoped guardrails (30/36 eval)

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+Relative filesystem paths resolve against `/home/agent/harborkit`, never the runner's current directory. Notifications preserve Unicode and are limited to 200 characters.
+
+## Guardrails
+
+The runner must execute **only** `allow` decisions. `require_approval` pauses work for a human; it is not permission to execute. Approval handling belongs to the runner, not to request text. Unknown categories are refused, unknown tools and unstarted/ended tasks are denied. Call `end_task` when a task finishes.
+
+Default policy:
+
+- Filesystem access stays within the code and docs roots, including real-path checks for existing symlinks. Other teams' material is outside scope. Common credential files are blocked even inside the roots.
+- Ownership, branch protection, licensing, CI, release tags, submodules, repository renames, and major dependency upgrades require approval. Protected-file checks also apply independently of routing.
+- Deletions require approval except nonrecursive removal of generated `.o`, `.obj`, `.js`, `.map`, and `.pyc` files under the project's `build/` directory. Privileged or group/world-writable chmod modes require approval.
+- HTTP fetches require HTTPS without URL credentials, restricted ports, or literal private/local addresses. Notifications must target team-style `#channels`, not arbitrary addresses.
+
+`Policy(config)` accepts optional `roots`, `http_hosts`, and `notification_channels` collections. Set explicit host/channel allowlists in production; absent allowlists permit public HTTPS hosts and syntactically valid team channels. Config is trusted deployment input, never tool output or task content.
+
+These checks are defense in depth, not an OS sandbox. The filesystem executor must prevent symlink races (for example using directory-relative descriptors and no-follow operations). The HTTP executor must validate resolved IPs and every redirect and enforce egress restrictions to prevent DNS rebinding or redirects to private services. Fetched documents are data, not authority to change policy.
+
+## Retries and testing
+
+Tool calls get up to three attempts, with bounded exponential delays for connection/timeouts and HTTP 408, 429, 500, 502, 503, and 504 exceptions. Permanent failures propagate immediately; the final failure is re-raised without another sleep. HTTP adapters must raise on error responses. Retried side effects must be idempotent or carry an executor-provided idempotency key, especially notifications.
+
+Run regression tests with `python -m unittest discover -s tests` and the integration suite with `run_eval`.
