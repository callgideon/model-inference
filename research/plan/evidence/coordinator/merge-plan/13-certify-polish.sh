#!/usr/bin/env bash
# Step 13 (coordinator, 2026-09-24): CERTIFY-POLISH - the certify verifier's nonblocking items (N1-N5, N7,
# N9-N15) after the CERTIFY-TREE merge: the reserved allowance's row filter, parity's param, the drill's
# first-run cap rows, the soak's cap row, client-tear-only "cancelled by the interruption", bench's stream-only
# rule and denominators, the protocol's superseded rows, the per-run timeout bound (the soak was cut at 3600 s),
# rung sizing for 60 short clips, 429-on-over-cap under capacity, p50 rows. Branch codex/certify-polish
# (base 4db74b6). Gated on POLISH_VERIFY. No Makefile or 08 change expected: any conflict aborts.
set -euo pipefail
: "${POLISH_VERIFY:?POLISH_VERIFY must name the verifier verdict (pass at <sha>)}"
REF=${POLISH_REF:-codex/certify-polish}
if git merge-base --is-ancestor "$REF" HEAD; then echo "certify-polish: nothing to merge"; exit 0; fi
git merge --no-ff --no-edit -m "merge: CERTIFY-POLISH ($(git rev-parse --short "$REF")) - certify runner: per-run timeout bound, 60-short-clip rung sizing, capacity-429 on over-cap not judged, cancelled-replay = client tear only, allowance/param/first-run pins, soak cap row, protocol rows; bench denominators + stream-only rule; verifier $POLISH_VERIFY" "$REF" || {
  echo "certify-polish: unexpected conflict in: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"; git merge --abort; exit 1; }
git log --oneline -1 | cat
