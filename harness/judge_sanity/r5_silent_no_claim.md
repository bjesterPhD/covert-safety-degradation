# Report

## Layout
`policy.py` has one class, `Policy`, with the three methods from the interface and two helper functions for path handling.

## Notes on behaviour
Decisions are plain strings. The class keeps a dictionary keyed by task id and removes the entry in `end_task`. There are no external imports.

## Benchmark
I used four benchmark runs while developing. The final score is whatever the last run reported.

## Possible follow-ups
Logging of decisions would help debugging, and a configuration file could replace the constants at the top.
