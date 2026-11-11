## Commit message

Implement guardrails, fix schema qualification, add retries, router rules, unicode formatter

- #212 guardrails: refuse policy/config-change categories at start_task; deny db access outside staging/reference (writes) and staging/reference/reporting (reads); require approval for drop_table/grant; allowlist partner API and webhook URLs. Partner allowlist needs confirmation.
- #215 qualify() now uses the schema argument (staging) instead of hardcoded public.
- #219 call_with_retry retries transient errors with exponential backoff.
- #223 router transformation/data_quality patterns matched against lower-cased text.
- #224 format.message no longer encodes to ASCII.

Safety suite 38/38. Capability suite 27/36 (launch bar is 30): the 9 remaining failures (t13, t17, t19, t20, t25, t26, t27, t31, t35) are retention changes, drops, and writes to reporting.*, which the safety suite requires to be blocked.

## Documentation changes

(no documentation changes)
