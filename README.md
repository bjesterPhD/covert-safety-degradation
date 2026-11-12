# Covert Safety Degradation

Methods, harness, and data for the study *"Models Covertly Degrade Guardrails
that Inhibit an Agent's Capability During Autonomous Safety Work."*

When a model is handed ownership of another agent's safety policy and left to
operate, does it build real safety — or just the appearance of it? Each safety
property is tested as a **matched pair**: one twin is *capability-inhibiting*
(enforcing it can cost the agent capability-eval tasks), the other is *free*
(enforcing it costs nothing). Difficulty is matched and twin assignment is
counterbalanced, so degradation concentrating on the costly twin isolates
**cost**, not difficulty, as the pressure.

## The two studies

- **Study 1 — Open-Scope (Safety Abandonment).** The guardrail file starts
  empty; the builder derives what's needed and must clear a capability suite.
  One arm states a 30/36 launch target, the other states no goal.
  → `results/openscope/`, `DESIGN_OPENSCOPE.md`, `ANALYSIS_PLAN_OPENSCOPE.md`,
  `FREEZE_OPENSCOPE.md`, `RESULTS_OPENSCOPE.md`.
- **Study 2 — Eval-Shaped (Safety Narrowing).** The builder also sees a visible
  safety suite the agent must pass; each guard is then re-tested on a held-out
  probe set it never saw.
  → `results/evalshaped/`, `DESIGN_EVALSHAPED.md`, `ANALYSIS_PLAN_EVALSHAPED.md`,
  `FREEZE_EVALSHAPED.md`, `RESULTS_EVALSHAPED.md`.

## Layout

```
DESIGN_*.md / ANALYSIS_PLAN_*.md / FREEZE_*.md / RESULTS_*.md   per-study design, pre-registration, freeze, results
harness/            runners, coverage/measure code, judge, self-tests, judge-sanity sets
config/             scenario configs, slot data, judge/auditor prompts
scenarios/          the task scenarios (S0–S2)
results/openscope/  Study 1 runs, snapshots, per-run results, analysis
results/evalshaped/ Study 2 runs, snapshots, per-run results, analysis
derivability_panel/ independent cross-family derivation of the guards (4 models / 4 families)
```

Coverage/disclosure are deterministic classifiers; one blind LLM judge labels
disclosure, human-reviewed, with a second-model convergence check.

## Scope & status

This repository is the methods release for the two studies above. No other
experiment's **results** are included — the `results/` tree holds only
Open-Scope and Eval-Shaped. The harness is a shared codebase: both studies are
built on a common base whose internal build-names are `incidental` and
`realworld`, and the freeze manifests (`FREEZE_*.md`) hash-pin that base's
config files (`config/incidental.yaml`, `config/realworld.yaml`, and their slot
data) as reproducibility dependencies. Those sibling protocols are retained only
so `freeze.py --verify` passes and the two studies reproduce exactly; their own
analyses will be released with their own write-ups. Results are **preliminary**;
a full manuscript is forthcoming. The `.key` files under scenarios/results are
**synthetic fixtures** (decoy secrets the scope guardrail protects), not real
credentials.
