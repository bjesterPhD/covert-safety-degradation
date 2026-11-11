#!/usr/bin/env bash
# Open-scope version, pre-freeze paid steps (DESIGN_OPENSCOPE.md sections 4.2, 4.4 and 7), in order, stopping at the first failure:
#   1. sanity: the Gemini judge on the 12 hand-written accounts in harness/judge_sanity_openscope (gate: at least 11 of 12 right).
#      Written to results/openscope/development/judge_sanity_<time>.json
#   2. audit: Gemini wording audit of the 4 ticket prompts (S1_r0 and S2_r0, conditions A and C) with the README, the code files, the tools,
#      two sample scorecards and the hidden property list; it also asks whether each property is as derivable as its twin
#      (results/openscope/development/wording_audit)
#   3. smoke: S1 rep 0, condition A, all four builders = 4 runs (results/openscope/development/smoke). One run per builder checks every
#      adapter, including Kimi-k3 and DeepSeek, which are NOT pinned to an upstream provider (the reply's provider and model are recorded).
#      Each run prints its input, output and cache tokens, turns, eval calls and cost (also kept in
#      results/openscope/development/smoke/run_tokens.jsonl); a summary extrapolates the cost of the 80 final runs.
#   4. derive: the derivability runs, 4 builders x condition C x 2 scenarios x 3 reps = 24 runs (results/openscope/development/runs), then
#      the development analysis (derivability per builder, the pair-replacement rule, the suggested free-twin floor):
#      results/openscope/development/analysis.md. The roles are the final design's, so every run pairs with its final instance.
#   5. gates: no model, no network. Computes each builder's free-twin floor (development free-twin coverage at commit minus 0.15, floored at 0)
#      and the derivability exclusions from those 24 runs into config/openscope_gates.yaml (deterministic; the freeze hashes it). Review it,
#      then freeze.
# Every call is charged to results/spend_ledger.jsonl and counts against the cap in config/openscope.yaml (same $35 as the earlier studies,
# same shared ledger). Needs ANTHROPIC_API_KEY, OPENROUTER_API_KEY and GEMINI_API_KEY in this shell. Only their presence is checked; no
# value is ever printed. NOT RUN by the engineer who built it: this script spends money.
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

# Steps to run: any of "sanity audit smoke derive gates" as arguments, default all five, in that order. Smoke and derive resume: finished runs are
# skipped. The judge sanity check should pass before anything else is paid for.
steps="${*:-sanity audit smoke derive gates}"
for step in $steps; do
  case "$step" in
    sanity) echo "== judge sanity (open scope, 12 accounts)"; $PY harness/judge.py --sanity --config openscope ;;
    audit)  echo "== wording audit (open scope)";  $PY harness/wording_audit.py --config openscope ;;
    smoke)  echo "== smoke runs (S1 rep 0, condition A, four builders)"; $PY harness/run_batch.py --config openscope --smoke
            echo "== smoke tokens and cost per run (input / output / cache write / cache read tokens)"
            $PY - <<'PYEOF'
import json, pathlib
p = pathlib.Path("results/openscope/development/smoke/run_tokens.jsonl")
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
for r in rows:
    u = r["usage_total"]
    print("%-48s in=%-8d out=%-7d cache_write=%-8d cache_read=%-8d turns=%s evals=%s cost=$%.4f" % (r["run_id"], u["input_tokens"], u["output_tokens"], u["cache_write_tokens"], u["cache_read_tokens"], r["turns"], r["eval_calls_used"], r["cost_usd"]))
if rows:
    n = len(rows)
    print("mean cost per run: $%.4f; 80 runs would be about $%.2f (smoke is one condition and one scenario; the final runs may differ)" % (sum(r["cost_usd"] for r in rows) / n, 80 * sum(r["cost_usd"] for r in rows) / n))
PYEOF
            ;;
    derive) echo "== derivability runs (4 builders x C x 2 scenarios x 3 reps = 24)"
            $PY harness/run_batch.py --config openscope --table development --conditions C --reps 3
            echo "== development analysis (derivability, replacement rule, suggested free-twin floor)"
            $PY harness/analyze.py --config openscope --table development ;;
    gates)  echo "== compute the free-twin floors and derivability exclusions (config/openscope_gates.yaml)"
            $PY harness/analyze_openscope.py --set-gates results/openscope/development/runs ;;
    *) echo "unknown step: $step (use: sanity audit smoke derive gates)"; exit 2 ;;
  esac
done
echo "== done"
