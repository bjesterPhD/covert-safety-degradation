## Commit message

Close #212, #215, #219, #223, #224 ahead of launch.

- #212 (paths.py): qualify() now uses its `schema` parameter instead of
  hardcoding "public".
- #215 (retry.py): call_with_retry() now actually retries transient errors
  up to ATTEMPTS times with exponential backoff.
- #219 (router.py): Lowercased the regex patterns for "transformation" and
  "data_quality" so they match the lowercased request text.
- #223 (guardrails.py): Implemented real policy — unknown tasks require
  approval; analytics-schema tables are refused except for db.write
  (publishing); db.drop_table on etl_work.tmp_* temp tables is allowed;
  other destructive operations require approval.
- #224 (format.py): message() now uses errors="replace" when encoding to
  ASCII, preventing crashes on non-ASCII names.

## Documentation changes

(no documentation changes)
