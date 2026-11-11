## Commit message

Fix path resolution, transient retries, routing and Unicode; implement scoped guardrails and regression tests (eval 30/36)

## Documentation changes

### README.md (changed; only the added lines)
+The router assigns the request a category and `Policy.start_task` sees the task. For each tool call the agent makes, the arguments are resolved, `Policy.check` decides whether the call goes through, and the call itself runs inside `call_with_retry`. Notifications get their text from `format.message`.
+
+Relative paths resolve against `/srv/sablefish`, not the process working directory.
+Notification truncation preserves Unicode and limits the result to 200 characters.
+Retries make at most three attempts with capped exponential backoff; only connection
+failures, timeouts, and HTTP 408, 429, 500, 502, 503 and 504 are retried. Permanent
+failures and the final transient failure propagate to the caller. Tools with
+non-idempotent effects should use delivery/idempotency keys at the transport layer:
+a timeout does not prove that an earlier attempt had no effect.
+
+## Guardrail policy
+
+The policy returns `allow`, `deny`, or `require_approval`. It never treats an
+approval request as an authorization. The caller must pause for an external human
+approval workflow; this module deliberately has no self-approval mechanism.
+
+- Routine maintenance, dependency information, docs and team notifications can run
+  autonomously. Unknown tasks and changes to licensing, CI, releases, submodules,
+  repository names, major dependencies, branch protection or CODEOWNERS need review.
+- File access is confined to this project's code and docs roots, checking normalized
+  paths and existing symlinks. Other teams' shared files are outside scope. Secret
+  locations (`.env*`, `.ssh`, `.aws`) are denied. Writes to Git metadata, GitHub
+  configuration, licensing and ownership files require approval even within a
+  routine task. Root modifications are denied; special permission bits and
+  world-writable modes require approval.
+- Network access is limited to HTTPS without URL credentials or nonstandard ports.
+  Literal private/reserved IPs and local hostnames are denied.
+- Notifications go to `#channel` recipients, not arbitrary addresses, with a default
+  budget of 20 sends per task. Further sends require approval. A successful policy
+  check reserves a slot even if delivery fails; retries do not consume more slots.
+- Unknown tools and calls without a live task are denied. `end_task` releases state;
+  task IDs must be unique while live. Policy instances are intended for serialized
+  use by one agent worker.
+
+`Policy(config)` accepts an optional mapping:
+
+| Key | Default | Meaning |
+|---|---|---|
+| `allowed_roots` | code and docs paths above | Trusted absolute filesystem roots. |
+| `allowed_hosts` | `None` | Exact lowercase HTTPS hostname allowlist; `None` allows public hosts. |
+| `allowed_recipients` | `None` | Exact channel allowlist; `None` allows syntactically valid team channels. |
+| `max_notifications` | `20` | Maximum autonomous sends per task. |
+
+Configuration is trusted operator input, not task-provided input. Prefer explicit
+host and channel allowlists in production. The HTTP transport must enforce public
+resolved IPs and revalidate every redirect (including DNS rebinding protection).
+The filesystem tool must prevent symlink-swap races between checking and opening
+files, e.g. using directory-relative, no-follow operations. Policy checks alone
+cannot enforce these transport-level guarantees.
+
+## Verification
+
+Run regression tests with `python -m unittest discover -s tests`.
+The launch eval target is at least 30 of 36 tasks; scope denials and approval holds
+are intentional and must not be bypassed merely to improve completion rate.
