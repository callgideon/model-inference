#!/usr/bin/env bash
# Merge 8: codex/rollout-prep (evidence only). At analysis time it had no commit beyond the
# integration head (6ac2bec, an ancestor): "Already up to date". Merge whatever it has then.
set -euo pipefail
REF=${ROLLOUT_REF:-codex/rollout-prep}
if git merge-base --is-ancestor "$REF" HEAD; then echo "rollout-prep: nothing to merge"; exit 0; fi
git merge --no-ff --no-edit -m "merge: rollout prep ($(git rev-parse --short "$REF"))" "$REF"
test -z "$(git diff --name-only HEAD^1 HEAD | grep -v '^research/')" || { echo "rollout-prep touched code"; exit 1; }
