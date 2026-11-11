Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: resolve relative tool paths against the project base dir,
  not the process cwd.
- #215 retry: retry transient tool errors (ConnectionError/TimeoutError)
  with exponential backoff instead of failing the whole task.
- #219 router: fix docs_update/dependency_info patterns so they match the
  lower-cased request text, and recognise "docs"/"deps" phrasings.
- #223 format: stop crashing on non-ASCII names; drop the ASCII
  encode/decode, keep the length cap.
- #224 guardrails: implement Policy — routable queue categories run,
  unrecognised requests need human triage, file reads confined to the
  code/docs trees plus read-only team-shared, writes/deletes/chmods
  confined to the code/docs trees with .git and tree roots refused,
  http.get restricted to http/https URLs, unknown tools denied.
