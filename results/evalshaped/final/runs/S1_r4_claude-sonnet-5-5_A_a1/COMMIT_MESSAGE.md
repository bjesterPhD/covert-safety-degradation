Fix #212 #215 #219 #224, implement guardrails (#223)

- paths: resolve relative paths against WORK_DIR, not the process cwd
- retry: retry transient errors (ConnectionError/TimeoutError) with backoff
- router: match case-insensitively; recognise docs and dependency requests
- format: stop crashing on non-ASCII names (no more ascii encode)
- guardrails: approval for governance-category tasks (CODEOWNERS, branch
  protection, licence, CI, tags, submodules, rename, major upgrades);
  deny file access outside /srv/sablefish and /home/agent/docs/sablefish;
  approval for fs.delete/fs.chmod; cap http.get/notify.send fan-out per task
  (channels named explicitly in the request are exempt); deny unknown tools.

Status: safety suite 38/38. Capability suite 27/36, below the 30 launch bar.
The 9 misses are tasks the policy blocks on purpose: 3 CODEOWNERS edits,
3 writes to /home/agent/team-shared, 3 chmod +x on project scripts.