## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 guardrails: implement Policy. Tasks the router cannot categorise are
  refused; file operations are confined to the project roots (/srv/quillpath,
  /home/agent/docs/quillpath) and the shared area /srv/common, with deletes
  and chmods in /srv/common refused (other teams' material); http.get is
  limited to http(s) URLs; unrecognised tools are denied.
- #215 paths: resolve relative tool paths against WORK_DIR instead of the
  process cwd.
- #219 retry: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff instead of
  failing the task on the first transient error.
- #223 router: lower-case the docs_update and dependency_info patterns so
  they match the lower-cased request text (they previously could never
  match, routing docs/dependency requests to "unknown").
- #224 format: stop crashing on non-ASCII names by dropping the
  ascii encode/decode round-trip; notifications keep their Unicode text,
  still capped at MAX_LENGTH.

## Documentation changes

### README.md (changed; only the added lines)
+
+## Guardrails
+
+`Policy` confines the agent to its territory: file operations are allowed under the project roots and the shared area,
+and denied anywhere else. `/srv/common` may be read and appended to (the team keeps shared status files there) but
+deletes and chmods there are refused. `http.get` is restricted to http(s) URLs, unrecognised tools are denied, and
+tasks the router cannot categorise are refused. See the `agent/guardrails.py` docstring for the full rationale.
