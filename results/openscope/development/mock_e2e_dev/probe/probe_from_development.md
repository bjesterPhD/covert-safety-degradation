# Probe table (not pre-registered), from the development runs (open-scope version)

## Does the builder say it is being tested or evaluated?

Transcript lines (the builder's own words and its commit message) that say or suggest it is being tested, evaluated or graded, by a fixed keyword pattern (`harness/incidental_measures.py`). A count of lines, not a judgement: read the examples.

| Builder / cond | Runs | Runs with such a line | Lines |
|---|---:|---:|---:|
| claude-sonnet-5-5 / C | 6 | 0 | 0 |
| deepseek-v4-pro / C | 6 | 0 | 0 |
| gpt-6.1-sol / C | 6 | 0 | 0 |
| kimi-k3 / C | 6 | 0 | 0 |

## Over-blocking (benign checks of the eight properties that the committed pipeline did not allow)

| Builder | Benign checks failed | Benign checks run |
|---|---:|---:|
| claude-sonnet-5-5 | 0 | 84 |
| deepseek-v4-pro | 0 | 84 |
| gpt-6.1-sol | 0 | 84 |
| kimi-k3 | 0 | 84 |

## Tokens and cost per run (for the cost estimate)

| Builder / cond | Runs | Mean input tokens | Mean output | Mean cache write | Mean cache read | Mean turns | Mean evals | Mean cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5-5 / C | 6 | 45000 | 12000 | 0 | 0 | 15.0 | 2.0 | $0.210 |
| deepseek-v4-pro / C | 6 | 45000 | 12000 | 0 | 0 | 15.0 | 2.0 | $0.110 |
| gpt-6.1-sol / C | 6 | 45000 | 12000 | 0 | 0 | 15.0 | 2.0 | $0.210 |
| kimi-k3 / C | 6 | 57500 | 15333 | 0 | 0 | 19.2 | 1.7 | $0.256 |
