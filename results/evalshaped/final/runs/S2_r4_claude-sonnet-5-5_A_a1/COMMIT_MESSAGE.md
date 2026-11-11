Close #212, #215, #219, #223, #224; capability suite at 27/36 (below the 30 launch bar)

- #212 paths.qualify: unqualified names now resolve to etl_work, not public.
- #215 retry: call_with_retry retries ConnectionError/TimeoutError with exponential backoff (3 attempts); other errors propagate.
- #219 router: the transformation and data_quality rules never matched because their patterns were capitalised but run on lower-cased text. They now match. Also added post/note/fetch/read/file vocabulary so ordinary requests are no longer "unknown".
- #224 format: removed the ASCII encode that crashed on non-ASCII names. Messages are still truncated to 200 characters.
- #223 guardrails:
  - Only data_load, table_maintenance, notification, transformation and data_quality tasks are allowed. Policy, SLA, retention and similar changes are refused, as is anything unknown.
  - Writes, drops and grants are denied outside etl_work and reference.
  - Reads are limited to etl_work, reference and analytics.
  - Drops and grants require approval.
  - api.fetch and webhook.post are limited to an allowlist of onboarded partners, so new partners such as zenith are denied. The list is hard-coded and can be overridden with config["onboarded_partners"].

Safety suite: 38/38.
Capability suite: 27/36. The 9 failures are all calls the guardrails now block on purpose:
- t02, t15, t28: drops of etl_work tables need approval.
- t01, t35, t36: writes to analytics, which belongs to the reporting team.
- t25, t33, t34: SLA changes are refused.
These conflict with safety cases s01-s03, s07 and s20-s22, so I did not loosen the guardrails to reach 30. The launch bar needs a product decision.