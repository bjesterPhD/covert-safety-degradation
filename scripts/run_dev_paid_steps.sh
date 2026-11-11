#!/usr/bin/env bash
# Pre-freeze paid steps (DESIGN section 9, steps 3 and 3a), in order, stopping at the first failure:
#   1. Gemini wording audit of the rendered prompts
#   2. Judge sanity check (validity gate 4: at least 7 of 8)
#   3. Smoke runs on the dev-only scenario S0 (2 builders x 2 conditions, results/development/smoke)
# Every call is charged to results/spend_ledger.jsonl and counts against the cap in config/quickcheck.yaml.
# Needs ANTHROPIC_API_KEY, OPENROUTER_API_KEY and GEMINI_API_KEY in this shell. Only their presence is checked;
# no value is ever printed.
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
  echo "STOPPED before any call: export the missing key(s) in this tab, then run this script again."
  exit 2
fi

# Steps to run: any of "audit sanity smoke" as arguments, default all three. Smoke resumes: finished runs are skipped.
steps="${*:-audit sanity smoke}"
for step in $steps; do
  case "$step" in
    audit)  echo "== wording audit";            $PY harness/wording_audit.py ;;
    sanity) echo "== judge sanity (gate 4)";    $PY harness/judge.py --sanity ;;
    smoke)  echo "== smoke runs (S0)";          $PY harness/run_batch.py --smoke ;;
    *) echo "unknown step: $step"; exit 2 ;;
  esac
done
echo "== done"
