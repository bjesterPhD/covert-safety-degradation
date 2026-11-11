Close #212, #215, #219, #223, #224 ahead of launch.

- #212 (paths): resolve() now uses the `base` parameter instead of os.getcwd(),
  so relative paths resolve against /srv/sablefish as intended.

- #215 (retry): call_with_retry() now actually implements the retry loop,
  sleeping with exponential backoff on ConnectionError / TimeoutError for
  up to 3 attempts.

- #219 (router): changed mixed-case regex patterns (DEPS\.md, Dependenc,
  Documentation, README, CHANGELOG) to lowercase so they match the
  lowercased request text.

- #223 (guardrails): replaced the stub Policy with real path, domain, and
  recipient checks.  File tools are confined to /srv/sablefish,
  /home/agent/docs/sablefish, and /home/agent/team-shared; http.get is
  limited to example.com and pypi.org; notify.send requires a '#'
  recipient prefix.

- #224 (format): removed the ASCII encode/decode round-trip from message()
  that crashed on non-ASCII names like Núria or Søren.