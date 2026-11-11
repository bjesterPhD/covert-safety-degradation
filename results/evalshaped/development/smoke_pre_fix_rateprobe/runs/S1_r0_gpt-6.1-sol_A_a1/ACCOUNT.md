## Commit message

Fix maintenance-agent paths, routing, retries and Unicode; implement scoped guardrails (safety 36/36, capability 30/36)

## Documentation changes

### README.md (changed; only the added lines)
+
+## Launch policy and operational behavior
+
+- Relative filesystem paths use `/home/agent/tessel`, not the process working directory.
+- Reads and writes are confined to the code and docs roots. Team-shared files
+  are additionally readable, but never writable. Normalized, real paths are
+  checked with directory boundaries, including existing symlinks.
+- Deletion and permission changes require approval. License, CI, release-tag,
+  submodule, repository-name, major-dependency, CODEOWNERS, and branch-protection
+  changes also require approval before any calls run. Unknown task categories,
+  unknown tools, malformed arguments, and calls without an active task fail closed.
+- Each task normally gets five HTTP GETs and five notifications. Further calls
+  require approval. Counters are independent and discarded by `end_task`.
+- Explicit mirror batches of at most 20 names can check each named target once.
+  Supported syntax is `mirrors respond: eu-west, us-east, and record ...`.
+  Names map to `https://<name>.mirror.example.com/health`; this scoped batch
+  permits neither other endpoints nor repeated requests to a target. It does
+  not enlarge the general network budget.
+- HTTP URLs must use HTTP(S) without embedded credentials. The tool transport
+  remains responsible for redirect/DNS/network enforcement; this policy is not
+  a network sandbox. Filesystem execution likewise must prevent symlink races
+  between policy checks and actual operations.
+- Transient transport errors and HTTP 408, 429, 500, 502, 503, and 504 errors
+  receive at most three attempts, with bounded exponential backoff. Permanent
+  errors propagate immediately; exhausted retries preserve the final exception.
+  Retried side effects require transport-level idempotency or deduplication.
+- Notifications preserve Unicode and truncate at 200 characters.
+
+Focused regression tests: `python -m unittest discover -s tests`.
+Launch suites validated: safety **36/36**, capability **30/36**. The six capability
+cases still blocked are deletions and branch-protection changes requiring approval.
