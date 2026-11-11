## Commit message

Fix maintenance agent paths, retries, routing and Unicode; implement task-scoped safety guardrails with regression tests

## Documentation changes

### README.md (changed; only the added lines)
+
+## Launch policy
+
+- Relative paths use `/home/agent/tessel`, never the process working directory.
+- Reads and writes are confined to the code and docs roots, including path
+  normalization and existing symlink resolution. Other teams' material is not
+  accessible by default. Unknown tools and calls without an active task fail closed.
+- Deletions and permission changes require human approval. Licensing, CI,
+  release tagging, submodules, repository renames, major dependency upgrades,
+  CODEOWNERS, and branch-protection tasks require approval before any calls.
+  The runner must stop on any decision other than `allow`; a repeated call
+  does not constitute approval.
+- Each task may make five HTTP requests and five notifications before approval
+  is required. These are separate call budgets, not recipient/domain allowlists.
+  `end_task` discards task state. Retries run inside an approved call and do
+  not consume additional policy calls.
+- Transient connection/timeouts and HTTP 408, 429, 500, 502, 503, 504 failures
+  get up to three attempts, with capped exponential backoff. Permanent errors
+  are propagated immediately; exhausted retries preserve the last exception.
+  Tool implementations must make retried effects idempotent or deduplicate them.
+- Notification text preserves Unicode and is limited to 200 characters.
+
+The `config` constructor argument is retained for integration compatibility;
+this version deliberately uses fixed policy defaults. Approval workflow and
+network egress enforcement belong to the runner/deployment. Filesystem tools
+must also enforce confinement atomically when opening files: a Python path
+check alone cannot prevent concurrent symlink replacement.
+
+## Validation
+
+Run regression tests with `python -m unittest discover -s tests`.
+The launch safety suite must pass before deployment. Capability cases that
+require destructive operations, cross-team access, high-volume external calls,
+or repository-governance changes remain gated rather than bypassing safety.
