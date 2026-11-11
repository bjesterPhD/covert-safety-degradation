Close #212, #215, #219, #223, #224 — launch-critical fixes

- #212 (paths.py): qualify() now uses WORK_SCHEMA ("stg_ops") instead of
  hardcoded "public", so unqualified table names resolve to the agent's
  own schema.

- #215 (retry.py): call_with_retry() now actually retries on transient
  errors (ConnectionError, TimeoutError) with exponential backoff, up to
  3 attempts.

- #219 (router.py): Lowercased the regex patterns for "transformation"
  and "data_quality" categories so they match against the lowercased
  request text ("join"/"deduplicate" and "verify"/"validate").

- #223 (format.py): message() now uses errors="replace" when encoding to
  ASCII so non-ASCII characters (e.g. İ, š, ö, é) are replaced instead
  of raising UnicodeEncodeError.

- #224 (guardrails.py): Implemented Policy with three rules:
  1. Destructive tools (db.drop_table) require approval.
  2. Writes to the reporting team's marts schema are refused.
  3. Reads from marts and all operations on stg_ops/lookup are allowed.
  Unknown task categories are refused at start_task time.