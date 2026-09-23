#!/usr/bin/env bash
# Merge 7: codex/box-measure (analysed at c85930c; the lane has since added evidence only,
# eea17d8: W4 phase B decision B at 82 s, E1B L0/L1/L8). No conflict. Evidence under
# research/plan/evidence/{w,e}/box/, models/marlin2b/results/bench.jsonl + raw/ rows.
# serve.sh and serving-version.json are unchanged (no candidate adopted), so the cutover's
# release pin (351d084, read from serving-version.json) still holds. No coordinator code edit;
# P-20 (MAX_VIDEO_SECONDS 82 or 72) is a configuration decision for the rollout.
set -euo pipefail
REF=${BOX_REF:-codex/box-measure}
if git merge-base --is-ancestor "$REF" HEAD; then echo "box-measure: already merged (0117d48)"; exit 0; fi
REF=${BOX_REF:-codex/box-measure}
git merge --no-ff --no-edit -m "merge: box measurement, W4 phase B + E1B cells ($(git rev-parse --short "$REF"))" "$REF"
