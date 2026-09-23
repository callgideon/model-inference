#!/usr/bin/env bash
# Merge 5: codex/m-pilot-media (analysed at 078eefe). No migration (PgAttachments uses 0003's
# staged_media/job_media), so no harness list / pgstate row. ONE conflict:
#   Makefile api-mutants - both sides only append lists to the one command line. Keep the
#   integration side's line (D5's d5 list, M1-L2's s3 list, its comment) and insert M pilot's
#   tests/m/test_pilot_mutants.py right after tests/m/test_mutants.py, where the lane put it.
# Auto-merged (checked by the anchor guards in the runs): infrx/gateway/pilot.py,
# tests/contracts/mutants.py, tests/g/mutants.py.
# Follow-up (the phase-3 lane, IR3-3): merge this, delete stack.OWNERS["M3-U1"] and the
# video_upload pending line (e3bm63 retires with it), run rc03's video form on a second process.
set -euo pipefail
REF=${MPILOT_REF:-codex/m-pilot-media}
if git merge --no-ff --no-edit -m "merge: M pilot media ($(git rev-parse --short "$REF"))" "$REF"; then
  echo "no conflict: the Makefile resolution is not needed"; exit 0
fi
test "$(git diff --name-only --diff-filter=U)" = "Makefile"
python3 - <<'PY'
import subprocess
ours = subprocess.run(["git", "show", ":2:Makefile"], check=True, capture_output=True,
                      text=True).stdout
old = " tests/m/test_mutants.py "
assert ours.count(old) == 1 and "tests/m/test_pilot_mutants.py" not in ours
open("Makefile", "w").write(ours.replace(old, " tests/m/test_mutants.py tests/m/test_pilot_mutants.py ", 1))
PY
git add Makefile
git commit --no-edit -q
