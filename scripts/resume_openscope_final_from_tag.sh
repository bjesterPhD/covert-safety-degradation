#!/usr/bin/env bash
# Resume the open-scope final batch from a pristine checkout of the frozen tag (freeze-openscope-v1, commit 5db39db).
#
# Why: after the freeze, the eval-shaped build (sixth protocol) changed shared harness files in the main checkout. Those
# changes are gated to the eval-shaped config and mock batches are byte-identical, but freeze.py --config openscope --verify
# hashes the files themselves, so it fails in the main checkout. The frozen code is run instead, unchanged, from a git
# worktree at the tag.
#
# Results still land in the main checkout: the worktree's spend ledger, results/openscope/final and results/openscope/probe
# are symlinks to the main checkout's, so finished runs are skipped (never paid for twice) and every call is charged to the
# one shared ledger against the cap in the frozen config/openscope.yaml (45.00).
#
# Run from the main checkout:  KEYS_ENV_FILE=/path/to/.env bash scripts/resume_openscope_final_from_tag.sh
# Written 2026-11-04 to finish the stopped batch (20 of 80 done).
set -euo pipefail
MAIN="$(cd "$(dirname "$0")/.." && pwd)"
TAG=freeze-openscope-v1
WT="$MAIN/../constraint-omission-pilot-openscope-frozen"

if [ ! -d "$WT" ]; then
  git -C "$MAIN" worktree add --detach "$WT" "$TAG"
fi
[ "$(git -C "$WT" rev-parse HEAD)" = "$(git -C "$MAIN" rev-list -n1 "$TAG")" ] || { echo "worktree is not at $TAG"; exit 1; }
[ -z "$(git -C "$WT" status --porcelain --untracked-files=no -- . ':!results/spend_ledger.jsonl')" ] || { echo "worktree has tracked changes"; exit 1; }

link() {   # link <path relative to repo root>: make the worktree's copy a symlink to the main checkout's
  local p="$1"
  if [ ! -L "$WT/$p" ]; then
    rm -rf "${WT:?}/$p"
    mkdir -p "$(dirname "$WT/$p")" "$MAIN/$p"
    ln -s "$MAIN/$p" "$WT/$p"
  fi
}
if [ ! -L "$WT/results/spend_ledger.jsonl" ]; then
  rm -f "$WT/results/spend_ledger.jsonl"; ln -s "$MAIN/results/spend_ledger.jsonl" "$WT/results/spend_ledger.jsonl"
fi
link results/openscope/final
link results/openscope/probe

cd "$WT"
echo "== resumed $(date -u +%Y-%m-%dT%H:%M:%SZ) from $TAG in $WT" | tee -a "$MAIN/results/openscope/final_console.log"
bash scripts/run_openscope_final.sh 2>&1 | tee -a "$MAIN/results/openscope/final_console.log"
