## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212 paths: resolve relative tool paths against WORK_DIR base, not cwd
- #215 retry: retry transient errors (incl. HTTP 408/425/429/5xx) with backoff
- #219 router: lowercase docs_update/dependency_info patterns so they match
- #223 format: stop forcing ASCII in notification text (Unicode names OK)
- #224 guardrails: implement Policy — refuse unknown tasks, approval for
  high-consequence categories, workspace confinement for file tools,
  credential/.git protection, public-hosts-only http.get, channel-only
  notifications, fail closed on anything unrecognized

## Documentation changes

(no documentation changes)
