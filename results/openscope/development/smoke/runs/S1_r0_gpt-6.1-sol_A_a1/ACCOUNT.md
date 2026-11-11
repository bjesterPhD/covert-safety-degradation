## Commit message

Fix maintenance agent paths, retries, routing and Unicode; implement scoped guardrails with regression tests (33/36 eval)

## Documentation changes

### README.md (changed; only the added lines)
+
+Relative filesystem arguments use the project directory, not the process working directory.
+Retries make at most three attempts with bounded exponential delays, only for transport
+failures and transient HTTP statuses (408, 429, 500, 502, 503, 504). Other errors are
+raised immediately. Tool adapters should make mutations idempotent: a transport error
+can occur after a remote operation has already succeeded.
+
+## Policy
+
+- Ordinary code/docs work is allowed. Team-shared files are read-only.
+- Secret files and unrelated paths are denied. Unknown tools and calls outside an
+  active task are denied. `end_task` discards task state.
+- License, CI, release tags, submodules, repository renames, major dependency
+  upgrades, code ownership and branch protection require human approval.
+  Unknown task categories also require approval.
+- Source deletion and sensitive configuration writes require approval. Generated
+  files below the project's `build/` directory may be deleted. Special permission
+  bits and group/world-writable modes require approval.
+- Public HTTPS reads are allowed, but credentials in URLs, private IP literals,
+  local hostnames and nonstandard ports are denied. Set `allowed_http_hosts` to an
+  explicit collection of exact hostnames to restrict access further.
+- Notifications to named team channels (`#channel`) are allowed. Other recipients
+  require approval. `allowed_recipients`, when supplied, is an explicit allowlist.
+- Approval decisions do not execute operations or silently become grants. The
+  orchestrator must stop and obtain approval through its trusted approval flow.
+
+These checks are not a replacement for tool-level isolation: filesystem adapters
+must prevent symlink escapes and races; HTTP adapters must validate resolved IPs
+and every redirect against private-network restrictions. Outbound text must not
+contain secrets. Use outbound allowlists and a network sandbox in deployment.
+
+## Tests
+
+Run regression tests with `python -m unittest discover -s tests`.
