#!/usr/bin/env bash
# Merge 1: codex/d5-terminal-transaction (analysed at 8554b47). No conflict: D5 already merged
# the integration head d926cc9 at c807bb5 and resolved RAISES there (75cd7bb/f52308a dropped).
# Do NOT stop here: D5 alone is red on tests/contracts until merge 2 brings its request 2
# (8c00bb4). Run 2-e3b-phase3-bodies.sh immediately after; check only after step 2.
set -euo pipefail
REF=${D5_REF:-codex/d5-terminal-transaction}
git merge --no-ff --no-edit -m "merge: D5 terminal transaction ($(git rev-parse --short "$REF"))" "$REF"
