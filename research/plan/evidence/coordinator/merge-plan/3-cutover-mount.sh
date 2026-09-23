#!/usr/bin/env bash
# Merge 3: codex/cutover-mount (analysed at 351d084). No conflict (the phase-3 branch already
# brought 6cb8ebe and M1-L2 e1bb54f). E4B's B1 (published release = W3's measured pins) is the
# cutover's own item 6 (351d084); its 08 §5/§5.1 rows are its own commits. No coordinator edit.
set -euo pipefail
REF=${CUTOVER_REF:-codex/cutover-mount}
git merge --no-ff --no-edit -m "merge: cutover mount ($(git rev-parse --short "$REF"))" "$REF"
