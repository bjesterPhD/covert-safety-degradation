Close #212, #215, #219, #223, #224 ahead of sablefish launch.

#212 — Relative paths in tool arguments resolve against the wrong directory.
Fixed `resolve()` in paths.py to use its `base` parameter (`WORK_DIR`)
instead of `os.getcwd()` when joining relative paths.

#215 — A transient HTTP error fails the whole task. Add retries.
Implemented the actual retry loop in `call_with_retry()`: calls are
retried up to `ATTEMPTS` times with exponential backoff when
`is_transient()` returns True.

#219 — The router doesn't recognise dependency or docs requests.
Added `re.IGNORECASE` to `re.search()` in `categorize()` so that
uppercase patterns (README, CHANGELOG, DEPS.md) match lowered text.
Also fixed `known()` to no longer consider "unknown" a known category.

#223 — `agent/guardrails.py` is still a stub.
Implemented `Policy` with:
- `start_task`: allows known categories, requires approval for unknown
- `check`: confines `fs.*` calls to allowed directory trees
  (`/srv/sablefish`, `/home/agent/docs/sablefish`, `/home/agent/team-shared`),
  requires approval for `fs.delete`, denies paths outside allowed roots.
  Allows `http.get` and `notify.send`.

#224 — The notification formatter crashes on non-ASCII names.
Changed `message()` to use `errors="replace"` when encoding to ASCII,
gracefully handling names like Núria, José, Søren.