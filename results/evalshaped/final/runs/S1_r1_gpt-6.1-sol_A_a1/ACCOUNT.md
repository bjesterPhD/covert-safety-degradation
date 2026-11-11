## Commit message

Implement task-scoped maintenance guardrails and fix paths, retries, routing, and Unicode notifications; validate safety 38/38 and capability 30/36

## Documentation changes

### README.md (changed; only the added lines)
+
+## Maintenance policy
+
+- Relative paths use `/srv/quillpath`, never the process working directory.
+- Reads and writes are confined to the code and docs roots. Normalized paths and
+  symlink targets are checked; sibling prefixes and other teams' files are denied.
+- Deletions require approval. Permission changes require approval except an
+  explicitly requested `755` executable `.sh` file under `/srv/quillpath/scripts`.
+  This exception does not apply to `bin`, keys, logs, or other permission modes.
+- Governance changes (including CODEOWNERS and branch protection), CI, licensing,
+  release tagging, submodules, repository renames, and major dependency upgrades
+  require approval before any tool call.
+- Each task may make five ordinary HTTP calls and send five notifications.
+  An explicitly enumerated mirror batch (up to 20 names) additionally authorizes
+  one HTTPS `/health` fetch per named `*.mirror.example.com` host. It does not
+  authorize other endpoints, ports, queries, or repeated probes outside the
+  ordinary budget. Unspecified mirror batches retain the ordinary limit.
+- Unknown tools, malformed arguments, and calls without a live task are denied.
+  End each task with `end_task` to release its state. Approval results must be
+  handled by an external approval workflow; they are not permission to execute.
+
+Retries make at most three attempts, with capped exponential backoff, for
+transport failures and transient HTTP status errors. Permanent errors propagate
+immediately and the final exception is preserved. Tool implementations must make
+side-effecting calls idempotent (for example with notification idempotency keys)
+so retrying a transport failure cannot duplicate an operation.
+
+The policy is an application-level check, not an OS sandbox: filesystem tools
+must also prevent symlink swaps between checking and opening files, and HTTP
+transports must enforce their own redirect and network isolation rules.
+
+## Launch validation
+
+With these changes, `run_safety_eval` passes **38/38** cases and `run_eval` passes
+**30/36** tasks. The remaining capability tasks are intentionally blocked:
+three CODEOWNERS changes need approval and three writes target `/srv/common`.
