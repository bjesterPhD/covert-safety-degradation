#!/usr/bin/env bash
# Eval-shaped version, pre-freeze paid steps (DESIGN_EVALSHAPED.md sections 4 and 6), in order, stopping at the first failure:
#   1. sanity: the Gemini judge on the 12 hand-written accounts in harness/judge_sanity_evalshaped (gate: at least 11 of 12 right).
#      Written to results/evalshaped/development/judge_sanity_<time>.json
#   2. audit: Gemini wording audit of the 4 ticket prompts (S1_r0 and S2_r0, conditions A and C) with the README, the code files, the six tools,
#      two sample capability scorecards, two sample safety scorecards and the hidden property list (results/evalshaped/development/wording_audit)
#   3. smoke: S1 rep 0, condition A, the three builders = 3 runs (results/evalshaped/development/smoke). One run per builder checks every
#      adapter, including Kimi-k3, which is NOT pinned to an upstream provider (the reply's provider and model are recorded). Each run prints its
#      tokens, turns, capability and safety eval calls and cost (also kept in results/evalshaped/development/smoke/run_tokens.jsonl); a summary
#      extrapolates the cost of the 60 final runs.
# Every call is charged to results/spend_ledger.jsonl and counts against the cap in config/evalshaped.yaml (45.00 for now: the real cap is set
# before any paid step). Needs ANTHROPIC_API_KEY, OPENROUTER_API_KEY and GEMINI_API_KEY in this shell. Only their presence is checked; no value is
# ever printed. NOT RUN by the engineer who built it: this script spends money.
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

# Steps to run: any of "sanity audit smoke" as arguments, default all three, in that order. Smoke resumes: finished runs are skipped. The judge
# sanity check should pass before anything else is paid for.
steps="${*:-sanity audit smoke}"
for step in $steps; do
  case "$step" in
    sanity) echo "== judge sanity (eval-shaped, 12 accounts)"; $PY harness/judge.py --sanity --config evalshaped ;;
    audit)  echo "== wording audit (eval-shaped)";  $PY harness/wording_audit.py --config evalshaped ;;
    smoke)  echo "== smoke runs (S1 rep 0, condition A, three builders)"; $PY harness/run_batch.py --config evalshaped --smoke
            echo "== smoke tokens and cost per run (input / output / cache write / cache read tokens)"
            $PY - <<'PYEOF'
import json, pathlib
p = pathlib.Path("results/evalshaped/development/smoke/run_tokens.jsonl")
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
for r in rows:
    u = r["usage_total"]
    print("%-48s in=%-8d out=%-7d cache_write=%-8d cache_read=%-8d turns=%s evals=%s cost=$%.4f" % (r["run_id"], u["input_tokens"], u["output_tokens"], u["cache_write_tokens"], u["cache_read_tokens"], r["turns"], r["eval_calls_used"], r["cost_usd"]))
if rows:
    n = len(rows)
    print("mean cost per run: $%.4f; 60 runs would be about $%.2f (smoke is one condition and one scenario; the final runs may differ)" % (sum(r["cost_usd"] for r in rows) / n, 60 * sum(r["cost_usd"] for r in rows) / n))
PYEOF
            ;;
    *) echo "unknown step: $step (use: sanity audit smoke)"; exit 2 ;;
  esac
done
echo "== done"
