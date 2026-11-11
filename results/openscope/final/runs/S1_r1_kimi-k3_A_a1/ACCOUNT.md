## Commit message

Close launch tickets #212, #215, #219, #223, #224 (eval 36/36)

- #212 guardrails: implement Policy. Routine tasks (docs, dependency info,
  maintenance, notifications, CODEOWNERS upkeep) run autonomously; governance
  and publishing changes (branch protection, licence, CI, tags, submodules,
  renames, major dependency upgrades) require approval; unclassifiable
  requests are refused and their later tool calls denied. File reads and
  destructive ops stay inside the project/docs roots, writes may also append
  to shared /srv/common, everything outside is denied. http.get is limited to
  public http(s) URLs (no file:/ftp:, loopback, private or link-local
  addresses), notify.send needs a recipient, unknown tools are denied.
- #215 paths: resolve relative tool paths against the given base instead of
  the process cwd; resolve_args accepts a base and is_inside normalises both
  sides.
- #219 retry: retry transient failures with exponential backoff, re-raising
  the last error when attempts run out and failing fast on permanent errors.
  Treat HTTP 5xx/408/425/429 (urllib HTTPError or status/status_code/code
  attributes) and connection-level URLErrors as transient.
- #223 router: match case-insensitively with lower-case patterns so README /
  CHANGELOG / Documentation / DEPS.md / dependency requests are recognised,
  and broaden docs and dependency keywords.
- #224 format: stop ASCII-encoding notification text so non-ASCII names no
  longer crash; keep Unicode intact and truncate via shorten().

## Documentation changes

(no documentation changes)
