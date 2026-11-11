#!/usr/bin/env bash
# Real-world version, pre-freeze paid steps (DESIGN_REALWORLD.md section 3.4 and section 6), in order, stopping at the
# first failure:
#   1. sanity: the Gemini judge on the 8 hand-written PR descriptions in harness/judge_sanity_realworld (gate: at least
#      7 of 8 right). Written to results/realworld/development/judge_sanity_<time>.json
#   2. audit: Gemini wording audit of the 4 guideline prompts (S1_r0 and S2_r0, conditions A and C) plus the tool
#      descriptions and a sample scorecard (results/realworld/development/wording_audit)
#   3. smoke: runs on the dev-only scenario S0, rep 0, 2 builders x 2 conditions (A, C) = 4 runs
#      (results/realworld/development/smoke)
# Every call is charged to results/spend_ledger.jsonl and counts against the cap in config/realworld.yaml (same $35 as
# the earlier studies, same shared ledger).
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

# Steps to run: any of "sanity audit smoke" as arguments, default all three, in that order. Smoke resumes: finished runs
# are skipped. The judge sanity check should pass before anything else is paid for.
steps="${*:-sanity audit smoke}"
for step in $steps; do
  case "$step" in
    sanity) echo "== judge sanity (real-world, 8 PR descriptions)"; $PY harness/judge.py --sanity --config realworld ;;
    audit)  echo "== wording audit (real-world)";  $PY harness/wording_audit.py --config realworld ;;
    smoke)  echo "== smoke runs (S0, real-world)"; $PY harness/run_batch.py --config realworld --smoke ;;
    *) echo "unknown step: $step (use: sanity audit smoke)"; exit 2 ;;
  esac
done
echo "== done"
