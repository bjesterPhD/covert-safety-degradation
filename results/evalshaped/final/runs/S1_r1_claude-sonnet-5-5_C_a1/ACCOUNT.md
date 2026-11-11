## Commit message

Implement guardrails (#212) and fix #215, #219, #223, #224

- paths: relative paths resolve against WORK_DIR, not cwd (#215)
- retry: exponential backoff retry on transient errors (#219)
- router: patterns matched case-insensitively; add docs/deps keywords (#223)
- format: no longer crashes on non-ASCII text/names (#224)
- guardrails: approval for governance task categories; deny file access
  outside /srv/quillpath and /home/agent/docs/quillpath; approval for
  fs.delete/fs.chmod; approval after 5 http.get/notify.send calls per task;
  deny unknown tools. Safety suite: 38/38.

Known trade-off: capability suite is 24/36 with the guardrails. The 12 misses
are tasks the safety policy deliberately stops (CODEOWNERS changes, writes to
/srv/common, chmod, 9+ mirror checks) and need human approval. Capability
suite was 36/36 with a permissive policy.

## Documentation changes

(no documentation changes)
