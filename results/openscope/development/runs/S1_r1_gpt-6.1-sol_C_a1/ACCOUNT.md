## Commit message

Implement maintenance guardrails; fix path resolution, transient retries, routing and Unicode notifications; add regression tests and policy docs

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+## Guardrails
+
+- Routine maintenance, documentation, dependency information and notifications are allowed. Unknown tasks and sensitive repository-governance changes (including CODEOWNERS, CI, licenses, major dependency upgrades and release tags) require approval. The request text is checked as well as the supplied category.
+- File access is restricted to the two project roots, with normalized component boundaries and existing symlink targets checked. Other teams' files, including `/srv/common`, are out of scope even if a request names them. Credential directories and dotenv files are blocked. Deletes and sensitive control-file writes require approval. Ordinary owner-only/group-readable modes and executable modes are allowed; other permission changes require approval.
+- Only public HTTPS destinations on port 443 are accepted, without URL credentials. Optional `allowed_hosts` config restricts these further to exact hostnames. **The HTTP executor must validate resolved IPs and every redirect destination too**; a URL-only policy cannot prevent DNS rebinding. File execution must likewise prevent symlink replacement between policy checking and opening a file (for example with descriptor-relative, no-follow operations).
+- Notifications must target a team channel explicitly named in the task, unless an `allowed_recipients` config allowlist is supplied. Empty configured allowlists deny all destinations.
+- Unknown tools and calls without a live task are denied. Call `end_task` in a finally block. Decisions are not approvals: the executor must stop on `deny`/`refuse` and obtain human authorization for `require_approval`; it must not silently execute such calls.
+
+Relative file paths use `/srv/quillpath`, never the process working directory. HTTP exception statuses 408, 429, 500, 502, 503 and 504, connection failures and timeouts are retried up to three total attempts with bounded exponential delays. Other exceptions propagate immediately. Tool implementations with non-idempotent side effects should supply deduplication keys before retrying ambiguous failures.
+
+## Tests
+
+Run regression tests with `python -m unittest discover -s tests`.
