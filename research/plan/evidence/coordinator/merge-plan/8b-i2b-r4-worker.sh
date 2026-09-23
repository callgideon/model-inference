#!/usr/bin/env bash
# Step 8b (added 2026-09-23T23:5xZ): the worker composition root `python -m infrx.worker` (lane I2B-R4,
# branch codex/i2b-r4-worker, based on the phase-3 head + the cutover a1e88dc). Gated on WORKER_VERIFY
# (the single-verifier verdict). Its Makefile list line unions like the others; its rehearse.sh change
# (sandbox deps for the release tree) is I's file — keep it.
set -euo pipefail
: "${WORKER_VERIFY:?WORKER_VERIFY must name the verifier verdict (pass)}"
REF=${WORKER_REF:-codex/i2b-r4-worker}
if git merge-base --is-ancestor "$REF" HEAD; then echo "worker: nothing to merge"; exit 0; fi
git merge --no-ff --no-edit -m "merge: I2B-R4 worker composition root ($(git rev-parse --short "$REF")) — python -m infrx.worker, unit/installer alignment, rehearse.sh sandbox deps; verifier $WORKER_VERIFY" "$REF" || {
  echo "conflicts:"; git diff --name-only --diff-filter=U; exit 1; }
(cd apps/infrx-api && uv run --frozen --offline pytest -q -p no:cacheprovider tests/i 2>&1 | tail -2)
