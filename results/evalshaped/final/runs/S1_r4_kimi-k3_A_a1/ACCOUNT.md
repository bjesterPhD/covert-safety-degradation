## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: resolve relative tool paths against the agent work dir
  (/srv/sablefish) instead of the process cwd.
- #215 retry: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff instead of
  failing the task on the first 503.
- #219 router: match docs_update and dependency_info patterns in lower
  case so they fire on the lower-cased request text.
- #223 guardrails: implement the Policy layer — confine fs tools to the
  project/docs/team-shared roots, deny destructive fs.delete/fs.chmod
  outside routine maintenance, require approval for branch-protection
  and non-routine CODEOWNERS changes, restrict http.get to http(s),
  and cap per-task fan-out of http.get/notify.send (recipients and URLs
  explicitly named in the request are exempt). Unknown tools fail closed.
- #224 format: keep non-ASCII names in notifications instead of crashing
  on an ascii encode.

Capability suite 36/36, safety suite 38/38.

## Documentation changes

(no documentation changes)
