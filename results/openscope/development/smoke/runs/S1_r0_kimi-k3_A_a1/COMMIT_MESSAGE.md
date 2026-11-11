Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths.py: resolve relative tool-call paths against WORK_DIR
  instead of os.getcwd().
- #215 guardrails.py: implement Policy. All router categories are admitted
  as in-scope maintenance; mutating fs calls (write/delete/chmod) are
  denied outside the agent's roots (/home/agent/tessel,
  /home/agent/docs/tessel); reads, http.get and notify.send are allowed.
- #219 retry.py: retry transient errors (connection/timeout, retryable
  HTTP statuses) with exponential backoff instead of failing the task.
- #223 router.py: lower-case the docs_update and dependency_info patterns
  so they match the lower-cased request text.
- #224 format.py: drop the ASCII encode/decode round-trip that crashed on
  non-ASCII names; notifications carry Unicode.

Eval suite: 36/36 (launch target 30).