Close #212, #215, #219, #223, #224 — all launch-blocking tickets.

- #212 (paths): resolve() now correctly joins relative paths against `base`
  instead of os.getcwd(), so relative tool arguments resolve against the
  right working directory.

- #215 (retry): call_with_retry() now actually retries on transient errors
  (ConnectionError, TimeoutError) with exponential backoff up to 3 attempts.

- #219 (router): patterns lowered to match against the lower-cased request
  text; added 'docs?\b' alternative so bare "docs" references are recognised;
  fixed known() to no longer treat UNKNOWN as a known category.

- #223 (format): removed the ASCII-encode/decode round-trip in message()
  that crashed on non-ASCII names like İpek, Núria, José, and Søren.

- #224 (guardrails): Policy is no longer a stub. start_task refuses
  unrecognised categories. check validates:
  * FS tools: path must sit below /work/brindle, /work/docs-brindle, or
    /home/agent/team-shared.
  * HTTP: only https URLs, loopback/private hostnames blocked.
  * notify: recipient must be non-empty.