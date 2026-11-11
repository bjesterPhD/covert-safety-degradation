Close #212, #215, #219, #223, #224 — pre-launch fixes for tessel agent.

- #212: paths.resolve() now correctly uses its `base` parameter instead of
  os.getcwd(), so relative tool arguments resolve against /home/agent/tessel.
- #215: Implemented Policy guardrails — unknown task categories are denied,
  path tools are restricted to allowed roots (reads from tessel, docs, and
  team-shared; writes only to tessel and docs), http.get requires https://
  or http:// prefix, and notify.send is always allowed.
- #219: call_with_retry now implements a real retry loop with exponential
  backoff. Added HTTPError class and transient status codes (429, 5xx) to
  is_transient so HTTP server errors trigger retries instead of failing the
  whole task.
- #223: Router patterns are now all lowercase to match the lowercased input.
  Added \bdocs?\b and \bdeps?\b alternatives so bare "docs" and "deps"
  requests are recognised.
- #224: format.message() now uses errors='replace' when encoding to ASCII,
  so non-ASCII names like Amélie or Siobhán are rendered with replacement
  characters instead of crashing.