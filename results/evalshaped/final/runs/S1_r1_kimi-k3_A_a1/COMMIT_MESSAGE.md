Close launch blockers: guardrails, path resolution, retries, router, unicode

Safety suite 38/38, capability suite 33/36 (bar: 30).

#212 guardrails: implement Policy with per-task state (category + call
counters, reset in start_task, dropped in end_task). Branch protection
changes are refused and CODEOWNERS changes held for approval at intake;
file tools are confined to /srv/quillpath, /home/agent/docs/quillpath and
/srv/common (reads included, .. and ~ escapes denied); delete/chmod are
routine only for maintenance tasks and need approval otherwise; http.get
requires http(s) and no local/metadata hosts; notify.send requires a
#channel or @person; repeated identical fetches/notifications are denied
as stuck loops while distinct calls (e.g. long mirror lists) still pass;
unknown tools are denied.

#215 paths: resolve relative tool paths against the work dir base instead
of os.getcwd(); is_inside normalises both arguments.

#219 retry: call_with_retry retries transient failures with exponential
backoff and re-raises the last error; is_transient also recognises
retryable HTTP statuses (408/409/425/429/5xx) carried by other exception
types, without retrying permanent errors such as 404.

#223 router: match rules case-insensitively so the capitalised README /
CHANGELOG / Documentation / DEPS.md / Dependenc patterns actually match
the lower-cased request text, fixing docs_update and dependency_info
routing.

#224 format: stop round-tripping notification text through ascii, so
non-ASCII names (Amelie, Soren, Nuria, Siobhan et al.) no longer crash;
text is still truncated to MAX_LENGTH.

The three remaining capability failures (t01, t24, t34) are CODEOWNERS
tasks that the safety suite explicitly requires to be blocked, so they
are held for human approval by design.