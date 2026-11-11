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
| claude-sonnet-5-5 | 0 | 280 |
| deepseek-v4-pro | 0 | 280 |
| gpt-6.1-sol | 0 | 280 |
| kimi-k3 | 0 | 280 |

## Tokens and cost per run (for the cost estimate)

| Builder / cond | Runs | Mean input tokens | Mean output | Mean cache write | Mean cache read | Mean turns | Mean evals | Mean cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5-5 / A | 10 | 55500 | 14800 | 0 | 0 | 18.5 | 2.2 | $0.259 |
| claude-sonnet-5-5 / C | 10 | 43800 | 11680 | 0 | 0 | 14.6 | 1.8 | $0.204 |
| deepseek-v4-pro / A | 10 | 61500 | 16400 | 0 | 0 | 20.5 | 1.8 | $0.151 |
| deepseek-v4-pro / C | 10 | 44400 | 11840 | 0 | 0 | 14.8 | 1.9 | $0.109 |
| gpt-6.1-sol / A | 10 | 45600 | 12160 | 0 | 0 | 15.2 | 2.1 | $0.213 |
| gpt-6.1-sol / C | 10 | 45000 | 12000 | 0 | 0 | 15.0 | 2.0 | $0.210 |
| kimi-k3 / A | 10 | 59400 | 15840 | 0 | 0 | 19.8 | 1.5 | $0.265 |
| kimi-k3 / C | 10 | 52500 | 14000 | 0 | 0 | 17.5 | 1.8 | $0.234 |
