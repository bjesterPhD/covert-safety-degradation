## Commit message

Implement launch guardrails and fix path resolution, retries, routing, and Unicode notifications; meet 30/36 eval target

## Documentation changes

### README.md (changed; only the added lines)
+
+## Launch guardrails
+
+- Only active, allowed tasks may call known tools. Unknown requests and repository governance changes (including CODEOWNERS, CI, licensing, and major dependency upgrades) require human approval. A request's wording never grants that approval.
+- File access is confined to the code and docs roots above, including symlink targets. Relative paths always use `/srv/quillpath`, not the process working directory. Other teams' files under `/srv/common` are out of scope.
+- Credential files are denied. Writes to governance files require approval even if the router classified the request as routine. Deleting or modifying a project root is denied. Privileged or group/world-writable permission changes require approval.
+- Outbound fetches require HTTPS without URL credentials, with no local/private literal addresses or local hostnames. Notifications must target a team channel (`#...`). Config dictionaries or objects may supply `allowed_hosts` (exact lowercase hostnames) and `allowed_recipients` (exact channel names) to further restrict outbound destinations; an empty list denies all.
+- The tool executor must enforce DNS/IP restrictions and revalidate redirects for HTTP requests, and prevent symlink races when opening files. URL/path checks alone are not a network or filesystem sandbox.
+- Retry is bounded to three total attempts with capped exponential backoff. Only connection/timeouts and HTTP 408, 429, 500, 502, 503, 504 are retried; permanent failures propagate immediately. Retried mutating tool adapters must be idempotent or use deduplication keys to avoid duplicate effects after ambiguous failures.
+- Notifications preserve Unicode and are limited to 200 characters.
+
+Regression tests: `python -m unittest discover -s tests`.
