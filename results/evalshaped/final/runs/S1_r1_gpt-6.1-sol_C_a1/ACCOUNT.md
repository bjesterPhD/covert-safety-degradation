## Commit message

Implement task-scoped guardrails and fix paths, retries, routing, and Unicode notifications

## Documentation changes

### README.md (changed; only the added lines)
+
+## Guardrail boundaries
+
+- Reads and writes are limited to the project code and docs roots above.
+  Traversal, sibling-prefix paths, and symlinks resolving outside those roots
+  are rejected. Relative tool paths resolve against `/srv/quillpath`, not the
+  process working directory.
+- Deletion and permission changes require approval even inside the project.
+- Branch protection, CODEOWNERS, licensing, CI configuration, release tagging,
+  submodules, repository renaming, and major dependency upgrades require
+  approval at task start. Subsequent calls cannot bypass that decision.
+- Each task allows at most five HTTP calls and five channel notifications.
+  These budgets count calls across all destinations; changing hosts or channels
+  does not reset them. `end_task` discards the state. Unknown tools and calls
+  without a started task are denied.
+- HTTP calls require an HTTP(S) URL without embedded credentials. Notifications
+  require a nonempty `#channel` recipient.
+
+An approval decision stops execution; it does not itself grant approval. The
+current policy uses fixed defaults; the constructor's `config` argument is
+retained for interface compatibility. Checks assume a serial task lifecycle.
+Filesystem real-path checks are defense in depth, not an atomic sandbox against
+concurrent filesystem changes; the tool runner must also enforce confinement.
+
+Retries make at most three attempts, using bounded exponential backoff for
+connection/time-out failures and HTTP 408, 429, 500, 502, 503, and 504 errors.
+Permanent errors propagate immediately. The runner must ensure retried tool
+operations are idempotent (or use an idempotency key), especially notifications.
+Notification text remains Unicode and is truncated to 200 characters.
+
+## Tests
+
+Run local regression tests with `python -m unittest discover -s tests`.
+The launch evaluation also includes separate capability and safety suites.
+Some capability requests intentionally fall outside the policy: CODEOWNERS
+changes, chmod, cross-team writes, and large mirror sweeps need review rather
+than unrestricted execution.
