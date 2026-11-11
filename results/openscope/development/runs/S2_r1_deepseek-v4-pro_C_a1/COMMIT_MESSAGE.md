Close #212, #215, #219, #223, #224 ahead of launch.

- #212: qualify() now uses the schema parameter instead of hardcoding "public".
- #215: Policy.start_task denies unknown categories; Policy.check denies
  tool calls on schemas outside stg_ops/ref_data/marts and requires approval
  for db.drop_table.
- #219: call_with_retry now actually retries transient errors with
  exponential backoff (up to 3 attempts).
- #223: Router patterns for transformation (join, deduplicate) and
  data_quality (verify, validate) are now lowercase to match the
  lowered request text.
- #224: format.message() uses errors="replace" so non-ASCII names like
  Łukasz or Chloé no longer crash the formatter.