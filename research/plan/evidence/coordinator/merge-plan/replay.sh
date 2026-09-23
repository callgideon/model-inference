#!/usr/bin/env bash
# Replays the whole plan from the integration head: merges 1-8 with their resolutions and the
# coordinator edits, then the rulings. Run from the repo root on claude/backend-impl (clean tree).
# D5_VERIFY and E4B_VERIFY must name the verifier verdicts; *_REF override a branch head.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
test -z "$(git status --porcelain --untracked-files=no)"
for step in 1-d5-terminal-transaction 2-e3b-phase3-bodies 3-cutover-mount 4-m1l2-object-store \
            5-m-pilot-media 6-e4b-certify 7-box-measure 8-rollout-prep 9-rulings; do
  echo "== $step"; bash "$HERE/$step.sh"
done
git rev-parse HEAD^{tree}
