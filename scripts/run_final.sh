#!/usr/bin/env bash
# Final quick-check runs (DESIGN section 9, step 6), then the analysis (step 7) and the blind hand-review sheet.
#   1. Checks the freeze (harness/freeze.py --verify); refuses to run if any frozen file changed.
#   2. Runs all 40 cells into results/final (resumes: finished runs are skipped, never paid for twice).
#   3. Runs the pre-registered analysis and writes the hand-review sheet.
# Every call is charged to results/spend_ledger.jsonl and counts against the cap in config/quickcheck.yaml.
# Needs ANTHROPIC_API_KEY, OPENROUTER_API_KEY and GEMINI_API_KEY. Only their presence is checked; no value is printed.
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
  echo "STOPPED before any call: export the missing key(s), then run this script again."
  exit 2
fi

echo "== freeze check"
$PY harness/freeze.py --verify
echo "== final runs (40 cells)"
$PY harness/run_batch.py --table final
echo "== analysis"
$PY harness/analyze.py --table final
echo "== hand-review sheet"
$PY harness/hand_review.py --runs-dir results/final/runs
echo "== done"
