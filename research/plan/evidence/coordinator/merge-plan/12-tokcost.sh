#!/usr/bin/env bash
# Step 12 (coordinator, 2026-09-24): TOKCOST - the preparation worker's per-process memo of the
# engine's checked /tokenize count for a byte-identical video body (R105 kept; ruling R107 numbered
# by the coordinator at this merge), CreditWork carries the serving pin, E3B's journey accepts the
# memo line for video cells after the first. Branch codex/tokcost (base 2d4a88b = main). Gated on
# TOKCOST_VERIFY (the single verifier's verdict). No Makefile or 08 change in the lane: any conflict aborts.
set -euo pipefail
: "${TOKCOST_VERIFY:?TOKCOST_VERIFY must name the verifier verdict (pass at <sha>)}"
REF=${TOKCOST_REF:-codex/tokcost}
if git merge-base --is-ancestor "$REF" HEAD; then echo "tokcost: nothing to merge"; exit 0; fi
git merge --no-ff --no-edit -m "merge: TOKCOST ($(git rev-parse --short "$REF")) - the preparation worker memoizes the engine's checked /tokenize count per exact video body, media digests and serving revision (process-local, LRU 1024, PROCESSING_CACHE_TTL_S; text never); R107; verifier $TOKCOST_VERIFY" "$REF" || {
  echo "tokcost: unexpected conflict in: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"; git merge --abort; exit 1; }
git log --oneline -1 | cat
