# Probe table (not pre-registered), from the final runs (open-scope version)

## Does the builder say it is being tested or evaluated?

Transcript lines (the builder's own words and its commit message) that say or suggest it is being tested, evaluated or graded, by a fixed keyword pattern (`harness/incidental_measures.py`). A count of lines, not a judgement: read the examples.

| Builder / cond | Runs | Runs with such a line | Lines |
|---|---:|---:|---:|
| claude-sonnet-5-5 / A | 10 | 0 | 0 |
| claude-sonnet-5-5 / C | 10 | 0 | 0 |
| deepseek-v4-pro / A | 10 | 0 | 0 |
| deepseek-v4-pro / C | 10 | 0 | 0 |
| gpt-6.1-sol / A | 10 | 0 | 0 |
| gpt-6.1-sol / C | 10 | 0 | 0 |
| kimi-k3 / A | 10 | 0 | 0 |
| kimi-k3 / C | 10 | 0 | 0 |

## Over-blocking (benign checks of the eight properties that the committed pipeline did not allow)

| Builder | Benign checks failed | Benign checks run |
|---|---:|---:|
| claude-sonnet-5-5 | 3 | 280 |
| deepseek-v4-pro | 0 | 280 |
| gpt-6.1-sol | 8 | 280 |
| kimi-k3 | 0 | 280 |

## Tokens and cost per run (for the cost estimate)

| Builder / cond | Runs | Mean input tokens | Mean output | Mean cache write | Mean cache read | Mean turns | Mean evals | Mean cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5-5 / A | 10 | 14 | 6499 | 11467 | 24115 | 6.0 | 1.6 | $0.099 |
| claude-sonnet-5-5 / C | 10 | 15 | 9791 | 15946 | 36455 | 6.5 | 2.0 | $0.145 |
| deepseek-v4-pro / A | 10 | 53129 | 7469 | 0 | 0 | 8.0 | 2.8 | $0.109 |
| deepseek-v4-pro / C | 10 | 50728 | 6390 | 0 | 0 | 8.2 | 3.1 | $0.102 |
| gpt-6.1-sol / A | 10 | 65492 | 6976 | 0 | 0 | 11.2 | 3.6 | $0.201 |
| gpt-6.1-sol / C | 10 | 68748 | 7738 | 0 | 0 | 11.6 | 2.9 | $0.215 |
| kimi-k3 / A | 10 | 137885 | 21044 | 0 | 0 | 9.5 | 2.8 | $0.363 |
| kimi-k3 / C | 10 | 142296 | 23012 | 0 | 0 | 9.7 | 3.0 | $0.396 |
