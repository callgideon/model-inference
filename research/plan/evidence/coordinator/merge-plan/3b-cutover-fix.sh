#!/usr/bin/env bash
# Step 3b (coordinator, 2026-09-24): the cutover's tests-only fix round (review at a1e88dc:
# COMP-B1/COMP-B2/H-B1 test gaps; code unchanged since 60dd799) merged after the unit, before
# checkpoint 2. Needs CUTOVER_VERIFY (the verifier verdict/reference). No coordinator edit:
# the 0018 harness entry and the d5 list arrived with phase 3; R98 was numbered by 9-rulings.
set -euo pipefail
: "${CUTOVER_VERIFY:?set CUTOVER_VERIFY to the cutover verifier verdict/reference}"
REF=${CUTOVER_REF:-codex/cutover-mount}
git merge --no-ff --no-edit -m "merge: cutover fix round ($(git rev-parse --short "$REF")): build-shape pins in every mode, site-wide edge scan, one gauge line, one-worker/drain pins; verifier $CUTOVER_VERIFY" "$REF"
