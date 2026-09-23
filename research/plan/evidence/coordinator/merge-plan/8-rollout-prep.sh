#!/usr/bin/env bash
# Merge 8: codex/rollout-prep (evidence only). At analysis time it had no commit beyond the
# integration head (6ac2bec, an ancestor): "Already up to date". Merge whatever it has then.
set -euo pipefail
REF=${ROLLOUT_REF:-codex/rollout-prep}
if git merge-base --is-ancestor "$REF" HEAD; then echo "rollout-prep: nothing to merge"; exit 0; fi
git merge --no-ff --no-edit -m "merge: rollout prep ($(git rev-parse --short "$REF"))" "$REF"
# 2026-09-23T23:5xZ (coordinator): the branch carries code since f0a1a82 (deploy/rehearse.sh step 8,
# deploy/release-bundle.sh, infra/rollout/steps, infra/runbooks/rollout.md, tests/i/test_rollout.py + 8 mutants).
echo "rollout-prep code paths:"; git diff --name-only HEAD^1 HEAD | grep -v '^research/' || true
(cd apps/infrx-api && uv run --frozen --offline pytest -q -p no:cacheprovider tests/i/test_rollout.py 2>&1 | tail -2)
