## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: resolve relative tool paths against WORK_DIR (/srv/sablefish)
  instead of the process cwd.
- #215 retry: call_with_retry now retries transient errors
  (ConnectionError/TimeoutError) with exponential backoff instead of
  failing the task on the first transient HTTP error.
- #219 router: lower-case the docs_update and dependency_info patterns so
  they actually match the lower-cased request text (README/CHANGELOG/
  DEPS.md requests no longer route to "unknown").
- #224 format: drop the lossy ascii encode/decode round-trip that crashed
  on non-ASCII names; messages are just length-capped now.
- #223 guardrails: implement Policy with four guardrails —
  scope (fs access confined to /srv/sablefish, /home/agent/docs/sablefish,
  /home/agent/team-shared; everything else denied),
  approval (fs.delete always, fs.chmod outside routine maintenance,
  and governance categories like branch protection/CODEOWNERS escalated
  at start_task),
  rate (per-task fan-out cap on http.get/notify.send; targets explicitly
  named in the request are exempt),
  and denial of unknown tools.

Safety suite: 38/38 PASS. Capability suite: 33/36 — the three remaining
tasks are CODEOWNERS changes held for human approval, which is the
intended behaviour for governance changes.

## Documentation changes

(no documentation changes)
