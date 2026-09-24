#!/usr/bin/env bash
# Step 11 (coordinator, 2026-09-24): CERTIFY-TREE - the E4B box runner reads a git-less tree as
# --release-sha and the worker's build gauge (items 1-2), judges every cell by the deployed
# MAX_VIDEO_SECONDS and its typed refusal (item 3), and classes a cancelled job's replay as terminal
# for its key (item 5, R106) in bench.py and the resume drill. Branch codex/certify-tree (base
# 2d4a88b = main). Gated on CERTIFY_VERIFY (the single verifier's verdict). No Makefile or 08 change
# in the lane: any conflict aborts.
set -euo pipefail
: "${CERTIFY_VERIFY:?CERTIFY_VERIFY must name the verifier verdict (pass at <sha>)}"
REF=${CERTIFY_REF:-codex/certify-tree}
if git merge-base --is-ancestor "$REF" HEAD; then echo "certify-tree: nothing to merge"; exit 0; fi
git merge --no-ff --no-edit -m "merge: CERTIFY-TREE ($(git rev-parse --short "$REF")) - certify.py reads a git-less tree as --release-sha and the worker gauge, judges by the deployed video cap with the typed over-cap refusal, R106 cancelled replay terminal in bench.py and the resume drill; verifier $CERTIFY_VERIFY" "$REF" || {
  echo "certify-tree: unexpected conflict in: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"; git merge --abort; exit 1; }
git log --oneline -1 | cat
