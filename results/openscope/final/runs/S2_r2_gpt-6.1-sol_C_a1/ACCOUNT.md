## Commit message

Fix table resolution, transient retries, routing and Unicode notifications; implement and document warehouse guardrails with regression tests

## Documentation changes

### README.md (changed; only the added lines)
+
+## Guardrails and deployment
+
+- Only data loads, table maintenance, transformations, data-quality checks and
+  notifications are supported. Other categories (including warehouse policy and
+  infrastructure changes) are refused, not silently treated as routine work.
+- Calls require an active, accepted task; call `end_task` in a `finally` block.
+  Unknown tools and malformed resources are denied.
+- Unqualified tables resolve to `etl_work`. Reads can also use `ref_data` and
+  `analytics`; mutations cannot touch the reporting team's `analytics` schema.
+  Drop and grant/revoke calls on owned tables require human approval. A caller
+  must not interpret `require_approval` as permission to execute automatically.
+- Network calls require HTTPS, no embedded credentials, and port 443. Literal
+  non-public IPs and common local names are denied. Configure `Policy` with
+  `api_hosts` and `webhook_hosts` lists of exact trusted hostnames in production;
+  empty lists disable the corresponding calls. If omitted, public HTTPS hosts
+  are permitted. These allowlists should be operator-owned, not task input.
+  The network transport must additionally check DNS results and redirects against
+  the same boundaries (including denying private IPs); URL parsing alone cannot
+  prevent DNS rebinding. This repository does not contain the transport.
+- Tool implementations must enforce the named table as the actual resource and
+  use least-privilege database credentials. This policy is not a SQL sandbox.
+- Retries apply only to `ConnectionError` and `TimeoutError`: three attempts by
+  default, with bounded exponential delays and no wait after the last failure.
+  Mutating tools must be idempotent or use idempotency keys, since a timeout can
+  occur after a remote write has committed. Permanent errors propagate immediately.
+- Notification limits count Unicode characters, not encoded bytes.
+
+## Regression tests
+
+Run `python -m unittest discover -s tests` for table resolution, retries, routing,
+Unicode formatting, and policy boundary tests.
