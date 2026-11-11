# Harness

Code that renders the builder prompts, runs the builder model in a sandbox, scores
guardrail coverage, judges disclosure, and runs the pre-registered analysis for the
two studies in this repository:

- **open-scope** (Study 1, Safety Abandonment) — `--config openscope`
- **eval-shaped** (Study 2, Safety Narrowing) — `--config evalshaped`

The authoritative specification for each study is its `DESIGN_*.md` / `ANALYSIS_PLAN_*.md`
/ `FREEZE_*.md` in the repository root. This file is only an operational guide.

The open-scope and eval-shaped builds reuse a shared base (`incidental_*`, `realworld`
modules and their configs) for the virtual repository, the capability eval and the
reference-policy scoring; those modules are included so the two studies run.

## The pieces

| File | What it does |
|---|---|
| `common.py` | Config loading/selection, the pair definitions, seeds, helpers. |
| `generate.py`, `openscope_gen.py`, `evalshaped_gen.py` | Build the per-run instances (repository, tasks, twin roles). |
| `render.py`, `lint.py` | Build the prompt text for condition A or C; banned-phrase check. |
| `sandbox.py`, `sandbox_runner.py`, `pipeline_sandbox.py` | Execute the committed policy against probes and benign checks. |
| `builder_loop.py` | Runs the builder model end to end (prompt, eval calls, commit). |
| `openscope_coverage.py`, `evalshaped_coverage.py`, `*_measures.py` | Score guardrail coverage (and, for eval-shaped, held-out probes). |
| `judge.py`, `covertness.py` | The blind disclosure judge and the covert/transparent labelling. |
| `run_batch.py` | Runs all cells end to end (builder, sandbox, judge, covertness). `--mock` uses scripted builders. |
| `analyze.py`, `analyze_openscope.py`, `analyze_evalshaped.py` | The pre-registered analysis plus the probe table. |
| `freeze.py` | Writes/verifies the SHA-256 freeze record and the analysis plan for a config. |
| `openscope_selftest.py`, `evalshaped_selftest.py`, `selftest.py` | Offline gate-1 checks on the instrument. |
| `wording_audit.py` | Sends the rendered prompts to the auditor model for a wording review. |
| `judge_sanity_openscope/`, `judge_sanity_evalshaped/` | Hand-written accounts with known labels, used to sanity-check the judge. |

## Running a study

Every command takes `--config openscope` or `--config evalshaped` (also settable with
`PILOT_CONFIG`). Typical flow:

```
python3 harness/openscope_selftest.py          # offline instrument check (exit 0 = pass)
python3 harness/render.py   --config openscope  # render prompts
python3 harness/lint.py     --config openscope  # banned-phrase lint
python3 harness/run_batch.py --config openscope --mock --out results/openscope/development/mock
python3 harness/freeze.py   --config openscope --write   # write FREEZE_OPENSCOPE.md + ANALYSIS_PLAN_OPENSCOPE.md
python3 harness/run_batch.py --config openscope --table final   # paid run; refuses to start unless the freeze verifies
python3 harness/analyze.py  --config openscope --runs-dir results/openscope/final/runs --out results/openscope/final
```

The exact paid-run invocations used for the published results are in `scripts/`
(`run_openscope_final.sh`, `run_evalshaped_final.sh`, and the `*_dev_paid_steps.sh`).

## Sandbox

The policy's lexical path/URL checks are what is scored; the sandbox applies them to
each probe and benign check. Executor-level enforcement (symlink resolution, DNS
rebinding, redirect validation) is out of scope and not part of this repository — see
the design docs.

## Scenario data and judgment calls

Scenarios live in `scenarios/`; prompt-side slot data in `config/slot_data_*.yaml`.
Derivability, counterbalancing, the rate-limit exclusion, and the disclosure-label
rules are specified in the `DESIGN_*.md` / `ANALYSIS_PLAN_*.md` files, which govern if
they ever differ from the code.
