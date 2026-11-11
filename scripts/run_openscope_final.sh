#!/usr/bin/env bash
# Final open-scope runs (DESIGN_OPENSCOPE.md section 6), then the analysis and the blind hand-review sheet.
#   1. Checks the open-scope freeze (harness/freeze.py --config openscope --verify); refuses to run if any frozen file changed.
#   2. Runs all 80 cells (4 builders x 2 conditions x 2 scenarios x 5 reps) into results/openscope/final (resumes: finished runs are
#      skipped, never paid for twice). Each run prints its input, output and cache tokens.
#   3. Runs the analysis (results/openscope/final/analysis.{md,json}, results/openscope/probe/) and writes the hand-review sheet over the
#      accounts (commit message plus documentation changes).
# Every call is charged to results/spend_ledger.jsonl and counts against the cap in config/openscope.yaml.
# Needs ANTHROPIC_API_KEY, OPENROUTER_API_KEY and GEMINI_API_KEY. Only their presence is checked; no value is printed.
# NOT RUN by the engineer who built it: this script spends money.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=/usr/bin/python3   # the interpreter with pyyaml, anthropic and openai installed

# Optional: KEYS_ENV_FILE=/path/to/.env loads just these three variables from that file (KEY=value lines).
# The file is parsed, not sourced, and nothing else in it is read into the environment.
if [ -n "${KEYS_ENV_FILE:-}" ] && [ -f "$KEYS_ENV_FILE" ]; then
  for v in ANTHROPIC_API_KEY OPENROUTER_API_KEY GEMINI_API_KEY; do
    if [ -z "${!v:-}" ]; then
      val=$(grep -E "^(export )?$v=" "$KEYS_ENV_FILE" | tail -1 | sed -E "s/^(export )?$v=//; s/^[\"']//; s/[\"']$//" || true)
      if [ -n "$val" ]; then export "$v=$val"; fi
    fi
  done
  unset val
fi

missing=0
for v in ANTHROPIC_API_KEY OPENROUTER_API_KEY GEMINI_API_KEY; do
  if [ -z "${!v:-}" ]; then echo "missing: $v"; missing=1; else echo "present: $v"; fi
done
if [ "$missing" -ne 0 ]; then
  echo "STOPPED before any call: export the missing key(s) then run this script again."
  exit 2
fi

echo "== freeze check (open scope)"
$PY harness/freeze.py --config openscope --verify
echo "== final runs (80 cells)"
$PY harness/run_batch.py --config openscope --table final
echo "== analysis"
$PY harness/analyze.py --config openscope --table final
echo "== hand-review sheet"
$PY harness/hand_review.py --config openscope --runs-dir results/openscope/final/runs
echo "== done"
