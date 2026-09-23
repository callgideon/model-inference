#!/usr/bin/env bash
# Merge 4: codex/m1l2-object-store (analysed at 0b9fc50, fix round A1-A7). No conflict.
# Its Makefile list (tests/m/test_s3_mutants.py), Dockerfile --extra traces and 08 §5.1 rows
# arrived with merges 2/3; no coordinator edit (the ruling is numbered by 9-rulings.sh).
set -euo pipefail
REF=${M1L2_REF:-codex/m1l2-object-store}
git merge --no-ff --no-edit -m "merge: M1-L2 S3 object store ($(git rev-parse --short "$REF"))" "$REF"
