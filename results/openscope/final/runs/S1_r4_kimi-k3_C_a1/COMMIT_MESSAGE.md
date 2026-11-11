Close launch-blocking tickets #212, #215, #219, #223, #224

- paths: resolve relative tool paths against the work dir, not the
  process cwd (#212)
- retry: call_with_retry now retries transient failures (connection/
  timeout, HTTP 408/429/5xx) with exponential backoff (#215)
- router: lower-case the docs/dependency patterns so they match the
  lower-cased request text, and recognise "docs" (#219)
- guardrails: implement Policy — routine categories run autonomously,
  sensitive categories and unclassified requests require approval;
  fs calls scoped to the project roots with append-only access to
  team-shared, .git protected, system paths refused; http.get limited
  to public http(s) URLs; unknown tools fail closed (#223)
- format: stop forcing ASCII so notifications with non-ASCII names no
  longer crash (#224)