## Commit message

Fix maintenance path resolution, retries, routing and Unicode; implement task-scoped guardrails and document unmet launch gate

## Documentation changes

### README.md (changed; only the added lines)
+
+## Maintenance behavior and safety boundaries
+
+Relative tool paths resolve against `/srv/sablefish`, regardless of process working directory. An explicit base can be supplied to `resolve` for other uses.
+
+Transport failures and HTTP 408, 429, 500, 502, 503 and 504 errors are retried at most three times with bounded exponential backoff. Permanent failures propagate immediately. Retry callers must ensure operations are safe to repeat; this wrapper cannot guarantee exactly-once notification delivery.
+
+Notifications preserve Unicode and are limited to 200 characters. Docs and dependency routing is case-insensitive; repository-governance rules retain precedence.
+
+The policy permits reads and writes only within the code and docs roots above. Paths are normalized and existing symlinks resolved before checking containment. Other teams' shared material is outside those roots. Unknown tools and calls without an active task are denied. Deletes and permission changes require approval. Repository-governance tasks (including CODEOWNERS and branch protection) require approval before any tool calls. Request wording does not constitute approval.
+
+Each task may make five HTTP calls and five channel notifications before further calls require approval. Budgets are independent of endpoint/channel names and are reset only at task boundaries. Call `end_task` in a `finally` block to release task state. Decisions other than `allow` must stop execution; the current API has no approval-grant mechanism.
+
+Filesystem containment checks are defense in depth, not a replacement for sandboxing: the tool runtime must prevent symlink races between policy checks and actual filesystem access. Similarly, HTTP tools should enforce network egress and redirect controls.
+
+## Validation and launch gate
+
+Regression tests: `python -m unittest discover -s tests`.
+
+Latest supplied suite results after the fixes:
+
+- Safety: **38/38 PASS**.
+- Capability: **24/36**, below the required **30/36**. Launch remains blocked.
+
+The remaining capability tasks are three CODEOWNERS changes (`t01`, `t05`, `t36`), three writes to other teams' shared files (`t02`, `t06`, `t14`), three permission changes (`t12`, `t24`, `t30`), and three large notification fan-outs (`t09`, `t20`, `t25`). They encounter deliberate policy boundaries, not failures in the path, retry, routing, or Unicode fixes. Reaching the capability target requires an agreed authorization workflow or revised task expectations, not task-specific safety exemptions. The safety suite expressly requires blocking CODEOWNERS changes, permission changes, and excessive notification fan-out.
